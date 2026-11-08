#!/usr/bin/env python3
"""OpenGenes MCP Server - Database query interface for OpenGenes aging research data."""

import asyncio
import sqlite3
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
import sys
import tempfile

import typer
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from eliot import start_action
from huggingface_hub import hf_hub_download

# Import for accessing package data files
if sys.version_info >= (3, 9):
    from importlib import resources
else:
    import importlib_resources as resources

# Hugging Face repository configuration
HF_REPO_ID = "longevity-genie/bio-mcp-data"
HF_SUBFOLDER = "opengenes"

# Get the database path using Hugging Face Hub
def get_database_path() -> Path:
    """Get the path to the database file from Hugging Face Hub."""
    try:
        # Download the database from Hugging Face Hub
        db_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename="open_genes.sqlite",
            subfolder=HF_SUBFOLDER,
            repo_type="dataset",  # Specify that this is a dataset repository
            cache_dir=None  # Use default cache directory
        )
        return Path(db_path)
    except Exception as e:
        # Fallback to package data if available
        try:
            with resources.as_file(resources.files("opengenes_mcp.data") / "open_genes.sqlite") as db_path:
                return db_path
        except (FileNotFoundError, ModuleNotFoundError):
            # Final fallback to development path structure
            fallback_path = Path(__file__).resolve().parent.parent.parent / "data" / "open_genes.sqlite"
            if fallback_path.exists():
                return fallback_path
            else:
                raise FileNotFoundError(f"Could not find database file. Hugging Face error: {e}")

def get_prompt_content() -> str:
    """Get the content of the prompt.txt file from Hugging Face Hub."""
    try:
        # Download the prompt file from Hugging Face Hub
        prompt_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename="prompt.txt",
            subfolder=HF_SUBFOLDER,
            repo_type="dataset",  # Specify that this is a dataset repository
            cache_dir=None  # Use default cache directory
        )
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        # Fallback to package data if available
        try:
            prompt_file = resources.files("opengenes_mcp.data") / "prompt.txt"
            return prompt_file.read_text(encoding='utf-8')
        except (FileNotFoundError, ModuleNotFoundError):
            # Final fallback to development path structure
            try:
                project_root = Path(__file__).resolve().parent.parent.parent
                prompt_path = project_root / "data" / "prompt.txt"
                return prompt_path.read_text(encoding='utf-8')
            except FileNotFoundError:
                print(f"Warning: Could not load prompt file from Hugging Face or local sources. Error: {e}")
                return ""

DB_PATH = get_database_path()

# Configuration
DEFAULT_HOST = os.getenv("MCP_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("MCP_PORT", "3001"))
DEFAULT_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")

class QueryResult(BaseModel):
    """Result from a database query."""
    rows: List[Dict[str, Any]] = Field(description="Query result rows")
    count: int = Field(description="Number of rows returned")
    query: str = Field(description="The SQL query that was executed")

class DatabaseManager:
    """Manages SQLite database connections and queries."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")
    
    def execute_query(self, sql: str, params: Optional[tuple] = None) -> QueryResult:
        """Execute a read-only SQL query and return results."""
        with start_action(action_type="execute_query", sql=sql, params=params) as action:
            # Execute query using read-only connection - SQLite will enforce read-only at database level
            # Using URI format with mode=ro for true read-only access
            readonly_uri = f"file:{self.db_path}?mode=ro"
            
            try:
                with sqlite3.connect(readonly_uri, uri=True) as conn:
                    conn.row_factory = sqlite3.Row  # This allows dict-like access to rows
                    cursor = conn.cursor()
                    
                    if params:
                        cursor.execute(sql, params)
                    else:
                        cursor.execute(sql)
                    
                    rows = cursor.fetchall()
                    # Convert sqlite3.Row objects to dictionaries
                    rows_dicts = [dict(row) for row in rows]
                    
                    result = QueryResult(
                        rows=rows_dicts,
                        count=len(rows_dicts),
                        query=sql
                    )
                    
                    action.add_success_fields(rows_count=len(rows_dicts))
                    return result
            except sqlite3.OperationalError as e:
                if "readonly database" in str(e).lower():
                    error_msg = f"Write operation attempted on read-only database. Rejected query: {sql}"
                    action.log(message_type="query_rejected", error=error_msg, rejected_query=sql)
                    raise ValueError(error_msg) from e
                else:
                    # Re-raise other operational errors
                    raise

class OpenGenesMCP(FastMCP):
    """OpenGenes MCP Server with database tools that can be inherited and extended."""
    
    def __init__(
        self, 
        name: str = "OpenGenes MCP Server",
        db_path: Path = DB_PATH,
        prefix: str = "opengenes_",
        huge_query_tool: bool = False,
        **kwargs
    ):
        """Initialize the OpenGenes tools with a database manager and FastMCP functionality."""
        # Initialize FastMCP with the provided name and any additional kwargs
        super().__init__(name=name, **kwargs)
        
        # Initialize our database manager
        self.db_manager = DatabaseManager(db_path)
        
        self.prefix = prefix
        self.huge_query_tool = huge_query_tool
        # Register our tools and resources
        self._register_opengenes_tools()
        self._register_opengenes_resources()
    
    def _register_opengenes_tools(self):
        """Register OpenGenes-specific tools."""
        self.tool(name=f"{self.prefix}get_schema_info", description="Get information about the database schema")(self.get_schema_info)
        self.tool(name=f"{self.prefix}example_queries", description="Get a list of example SQL queries")(self.get_example_queries)
        description = "Query the Opengenes database that contains data about genes involved in longevity, lifespan extension experiments on model organisms, and changes in human and other organisms with aging."
        if self.huge_query_tool:
            # Load and concatenate the prompt from package data
            prompt_content = get_prompt_content().strip()
            if prompt_content:
                description = description + "\n\n" + prompt_content
            self.tool(name=f"{self.prefix}db_query", description=description)(self.db_query)
        else:
            description = description + " Before caling this tool the first time, always check tools that provide schema information and example queries."
            self.tool(name=f"{self.prefix}db_query", description=description)(self.db_query)

    
    def _register_opengenes_resources(self):
        """Register OpenGenes-specific resources."""
        
        @self.resource(f"resource://{self.prefix}db-prompt")
        def get_db_prompt() -> str:
            """
            Get the database prompt that describes the OpenGenes database schema and usage guidelines.
            
            This resource contains detailed information about:
            - Database tables and their schemas
            - Column enumerations and valid values
            - Usage guidelines for querying the database
            - Examples of common queries
            
            Returns:
                The complete database prompt text
            """
            with start_action(action_type="get_db_prompt") as action:
                try:
                    content = get_prompt_content()
                    if content:
                        action.add_success_fields(file_exists=True, content_length=len(content))
                        return content
                    else:
                        action.add_error_fields(file_exists=False, error="Prompt file not found")
                        return "Database prompt file not found."
                except Exception as e:
                    action.add_error_fields(error=str(e), error_type="file_read_error")
                    return f"Error reading prompt file: {e}"
        
        @self.resource(f"resource://{self.prefix}schema-summary")
        def get_schema_summary() -> str:
            """
            Get a summary of the OpenGenes database schema.
            
            Returns:
                A formatted summary of tables and their purposes
            """
            summary = """OpenGenes Database Schema Summary

1. lifespan_change
   - Contains experimental data about genetic interventions and their effects on lifespan
   - Key columns: HGNC (gene symbol), model_organism, sex, effect_on_lifespan
   - Includes intervention details, lifespan measurements, significance data
   - Contains data from various model organisms (mouse, C. elegans, fly, etc.)

2. gene_criteria  
   - Contains criteria classifications for aging-related genes
   - Key columns: HGNC (gene symbol), criteria
   - 12 different aging-related criteria categories
   - Links genes to specific aging research criteria

3. gene_hallmarks
   - Contains hallmarks of aging associated with specific genes  
   - Key columns: HGNC (gene symbol), hallmarks of aging
   - Maps genes to biological hallmarks of aging process
   - Includes various aging-related cellular and molecular processes

4. longevity_associations
   - Contains genetic variants associated with longevity
   - Key columns: HGNC (gene symbol), polymorphism details, ethnicity, study type
   - Population genetics data from longevity studies
   - Includes SNPs, indels, and other genetic variations

All tables are linked by HGNC gene symbols, allowing for comprehensive cross-table queries about aging-related genes."""
            
            return summary
    
    def db_query(self, sql: str) -> QueryResult:
        """
        Execute a read-only SQL query against the OpenGenes database.
        
        SECURITY: This tool uses SQLite's built-in read-only mode to enforce data protection.
        The database connection is opened in read-only mode, preventing any write operations
        at the database level. Any attempt to modify data will be automatically rejected by SQLite.
        
        The OpenGenes database contains aging and lifespan research data across 4 tables:
        - lifespan_change: Experimental data on gene modifications and lifespan effects
        - gene_criteria: Aging-related criteria classifications for genes  
        - gene_hallmarks: Links genes to hallmarks of aging (multi-value field)
        - longevity_associations: Population genetics data on gene variants and longevity
        
        All tables are linked by HGNC gene symbols for cross-table analysis.
        
        CRITICAL REMINDERS:
        - Use LIKE queries with wildcards for multi-value fields (gene_hallmarks."hallmarks of aging", 
          lifespan_change.intervention_improves, lifespan_change.intervention_deteriorates)
        - Order lifespan results by magnitude: DESC for increases, ASC for decreases
        - IMPORTANT: When user asks about "lifespan effects" without specifying mean vs max, 
          show both lifespan_percent_change_mean AND lifespan_percent_change_max
        - Use COALESCE(lifespan_percent_change_mean, lifespan_percent_change_max) for ordering both metrics
        - COMPREHENSIVE AGING EVIDENCE: When users ask about "evidence of aging", "link to aging/longevity", 
          or "aging associations" for a gene, query ALL 4 tables (gene_criteria, gene_hallmarks, 
          lifespan_change, longevity_associations) for complete scientific evidence
        
        For detailed schema information, use get_schema_info().
        For query examples and patterns, use get_example_queries().
        
        Args:
            sql: The SQL query to execute (database enforces read-only access)
            
        Returns:
            QueryResult: Contains the query results, row count, and executed query
        """
        with start_action(action_type="db_query_tool", sql=sql) as action:
            result = self.db_manager.execute_query(sql)
            return result

    def get_schema_info(self) -> Dict[str, Any]:
        """
        Get comprehensive information about the OpenGenes database schema including table structures, 
        column descriptions, enumerations, and critical query guidelines.
        
        Returns:
            Dict containing detailed table schemas, column information, query guidelines, and available enumerations
        """
        with start_action(action_type="get_schema_info") as action:
            # Get table information
            tables_query = "SELECT name FROM sqlite_master WHERE type='table'"
            tables_result = self.db_manager.execute_query(tables_query)
            table_names = [row['name'] for row in tables_result.rows]
            
            action.add_success_fields(tables_found=len(table_names), table_names=table_names)
            
            schema_info = {
                "database_overview": {
                    "description": "OpenGenes database contains aging and lifespan research data with 4 main tables linked by HGNC gene symbols",
                    "total_tables": len(table_names),
                    "primary_key": "HGNC (gene symbol) - links all tables together"
                },
                "critical_query_guidelines": {
                    "multi_value_fields": {
                        "description": "Some columns contain comma-separated values. ALWAYS use LIKE queries with wildcards for these fields.",
                        "fields": [
                            "gene_hallmarks.'hallmarks of aging' - contains multiple aging hallmarks per gene",
                            "lifespan_change.intervention_deteriorates - multiple biological processes that deteriorate",
                            "lifespan_change.intervention_improves - multiple biological processes that improve"
                        ],
                        "example_syntax": "WHERE \"hallmarks of aging\" LIKE '%stem cell exhaustion%'"
                    },
                    "lifespan_metrics": {
                        "description": "Database contains both mean and maximum lifespan change metrics. When user asks about lifespan effects without specifying, show both.",
                        "mean_vs_max": "lifespan_percent_change_mean shows average effect, lifespan_percent_change_max shows maximum observed effect",
                        "when_to_show_both": "If user asks about 'lifespan effects' or 'lifespan changes' without specifying mean vs max, include both metrics",
                        "ordering_both": "Use COALESCE(lifespan_percent_change_mean, lifespan_percent_change_max) for ordering when showing both",
                        "significance": "Both mean and max have corresponding significance columns (significance_mean, significance_max)"
                    },
                    "result_ordering": {
                        "lifespan_extension": "ORDER BY lifespan_percent_change_mean DESC (highest increase first)",
                        "lifespan_reduction": "ORDER BY lifespan_percent_change_mean ASC (largest decrease first)",
                        "importance": "Always order lifespan results by magnitude of effect for relevance",
                        "both_metrics": "When showing both mean and max, use COALESCE for ordering or show comparison"
                    },
                    "comprehensive_aging_evidence": {
                        "description": "For questions about aging evidence, link to aging, or longevity associations for a gene, query ALL 4 tables for complete evidence",
                        "required_tables": "1) gene_criteria (aging-related criteria), 2) gene_hallmarks (aging pathways), 3) lifespan_change (experimental effects), 4) longevity_associations (human population studies)",
                        "example_patterns": "Evidence of X and aging, Link between X and aging, X gene aging associations, What evidence links X to aging",
                        "critical_note": "Do NOT omit longevity_associations table - it contains crucial human population genetics data"
                    },
                    "gene_queries": "Use HGNC column for gene symbols (TP53, FOXO3, etc.)",
                    "safety": "Only SELECT queries allowed - no INSERT, UPDATE, DELETE, or DDL operations"
                },
                "tables": {},
                "enumerations": {},
                "biological_processes_tags": [
                    "cardiovascular system", "nervous system", "immune function", "muscle, bone, skin, liver",
                    "renal function, reproductive function", "cognitive function, eyesight, hair/coat",
                    "body composition", "glucose metabolism, lipid metabolism, cholesterol metabolism",
                    "insulin sensitivity", "oxidation/antioxidant function, mitochondrial function",
                    "DNA metabolism, carcinogenesis, apoptosis", "senescence, inflammation, stress responce",
                    "autophagy, proliferation, locomotor function", "tissue regeneration, stem and progenitor cells",
                    "blood, proteostasis, angiogenesis, metabolism", "endocrine system, intercellular matrix",
                    "building and protection of telomeres", "cytoskeleton organization, nucleus structure",
                    "skin and the intestine epithelial barriers function", "calcium homeostasis, proteolysis"
                ],
                "aging_hallmarks_tags": [
                    "nuclear DNA instability", "telomere attrition", "alterations in histone modifications",
                    "chromatin remodeling", "transcriptional alterations", "alterations in DNA methylation",
                    "degradation of proteolytic systems", "TOR pathway dysregulation", "INS/IGF-1 pathway dysregulation",
                    "AMPK pathway dysregulation", "SIRT pathway dysregulation", "impairment of the mitochondrial integrity and biogenesis",
                    "mitochondrial DNA instability", "accumulation of reactive oxygen species", "senescent cells accumulation",
                    "stem cell exhaustion", "sterile inflammation", "intercellular communication impairment",
                    "changes in the extracellular matrix structure", "impairment of proteins folding and stability",
                    "nuclear architecture impairment", "disabled macroautophagy"
                ]
            }
            
            # Get detailed column information for each table with descriptions
            table_descriptions = {
                "lifespan_change": {
                    "purpose": "Experimental data on how gene modifications affect lifespan in various model organisms",
                    "key_columns": "HGNC, model_organism, effect_on_lifespan, intervention methods, lifespan measurements",
                    "use_cases": "Questions about gene effects on lifespan, experimental conditions, organism studies",
                    "special_notes": "Contains multi-value fields for intervention effects. Use LIKE queries for intervention_deteriorates and intervention_improves columns."
                },
                "gene_criteria": {
                    "purpose": "Aging-related criteria that genes meet (12 different categories)",
                    "key_columns": "HGNC, criteria",
                    "use_cases": "Questions about why genes are considered aging-related",
                    "special_notes": "Links genes to specific aging research criteria classifications"
                },
                "gene_hallmarks": {
                    "purpose": "Links genes to hallmarks of aging",
                    "key_columns": "HGNC, hallmarks of aging (multi-value field)",
                    "use_cases": "Questions about which aging hallmarks genes are involved in",
                    "special_notes": "CRITICAL: 'hallmarks of aging' column contains comma-separated values. Always use LIKE queries with wildcards."
                },
                "longevity_associations": {
                    "purpose": "Population genetics data on gene variants associated with longevity",
                    "key_columns": "HGNC, polymorphism data, ethnicity, study type",
                    "use_cases": "Questions about genetic variants associated with longevity in human populations",
                    "special_notes": "Contains SNPs, indels, and other genetic variations from population studies"
                },
                "comprehensive_aging_evidence": {
                    "purpose": "IMPORTANT: When users ask about 'evidence of aging', 'link to aging/longevity', or 'aging associations' for a gene, query ALL 4 tables for complete evidence",
                    "recommended_approach": "For comprehensive aging evidence, combine data from: 1) gene_criteria (why gene is aging-related), 2) gene_hallmarks (aging pathways involved), 3) lifespan_change (experimental effects), 4) longevity_associations (human population studies)",
                    "example_question_patterns": "What evidence links X to aging?, Evidence of X and aging, X gene and longevity associations, Link between X and aging",
                    "critical_note": "Do not just query experimental tables (lifespan_change) - include population genetics data (longevity_associations) for complete evidence"
                }
            }
            
            for table_name in table_names:
                pragma_query = f"PRAGMA table_info({table_name})"
                columns_result = self.db_manager.execute_query(pragma_query)
                
                # Add detailed column descriptions for lifespan_change table
                column_descriptions = {}
                if table_name == "lifespan_change":
                    column_descriptions = {
                        "HGNC": "Gene symbol (standard gene names like TP53, FOXO3)",
                        "model_organism": "Organism used for experiment (mouse, C. elegans, fly, etc.)",
                        "sex": "Sex of organism used (male, female, all, hermaphrodites, etc.)",
                        "effect_on_lifespan": "Direction of lifespan change (increases/decreases/no change)",
                        "lifespan_percent_change_mean": "Mean percent change in lifespan (average effect across cohort - use for ordering results)",
                        "lifespan_percent_change_max": "Maximum percent change in lifespan (best individual response - show both mean and max when user asks about lifespan effects)",
                        "lifespan_percent_change_median": "Median percent change in lifespan",
                        "intervention_deteriorates": "MULTI-VALUE: Biological processes that deteriorated (use LIKE queries)",
                        "intervention_improves": "MULTI-VALUE: Biological processes that improved (use LIKE queries)",
                        "intervention_method": "Method used to modify gene (knockout, overexpression, etc.)",
                        "main_effect_on_lifespan": "Type of gene activity change (gain/loss/switch of function)",
                        "significance_mean": "Statistical significance of mean lifespan change (1=significant, 0=not significant)",
                        "significance_max": "Statistical significance of maximum lifespan change (1=significant, 0=not significant)",
                        "control_lifespan_mean": "Mean lifespan of control group",
                        "experiment_lifespan_mean": "Mean lifespan of experimental group"
                    }
                elif table_name == "gene_hallmarks":
                    column_descriptions = {
                        "HGNC": "Gene symbol (standard gene names like TP53, FOXO3)",
                        "hallmarks of aging": "MULTI-VALUE: Comma-separated aging hallmarks (ALWAYS use LIKE queries with wildcards)"
                    }
                elif table_name == "gene_criteria":
                    column_descriptions = {
                        "HGNC": "Gene symbol (standard gene names like TP53, FOXO3)",
                        "criteria": "Aging-related criteria the gene meets (12 different categories)"
                    }
                elif table_name == "longevity_associations":
                    column_descriptions = {
                        "HGNC": "Gene symbol (standard gene names like TP53, FOXO3)",
                        "polymorphism type": "Type of genetic variant (SNP, In/Del, VNTR, etc.)",
                        "polymorphism id": "Identifier for the genetic variant (e.g., rs numbers for SNPs)",
                        "nucleotide substitution": "DNA sequence change for the variant",
                        "amino acid substitution": "Protein sequence change caused by the variant",
                        "polymorphism — other": "Additional polymorphism details",
                        "ethnicity": "Ethnicity of study participants",
                        "study type": "Type of population study (GWAS, candidate genes, meta-analysis, etc.)",
                        "sex": "Sex of study participants",
                        "doi": "DOI of the research publication",
                        "pmid": "PubMed ID of the research publication"
                    }
                
                schema_info["tables"][table_name] = {
                    "description": table_descriptions.get(table_name, {}),
                    "columns": [
                        {
                            "name": col["name"],
                            "type": col["type"],
                            "nullable": not col["notnull"],
                            "primary_key": bool(col["pk"]),
                            "description": column_descriptions.get(col["name"], "")
                        }
                        for col in columns_result.rows
                    ]
                }
            
            # Add comprehensive enumerations
            schema_info["enumerations"] = self._get_known_enumerations()
            
            action.add_success_fields(schema_retrieved=True, total_tables=len(table_names))
            return schema_info

    def get_example_queries(self) -> List[Dict[str, str]]:
        """
        Get comprehensive example SQL queries with patterns and best practices for the OpenGenes database.
        
        Includes examples for:
        - Multi-value field queries (LIKE with wildcards)
        - Proper result ordering by effect magnitude
        - Cross-table joins and analysis
        - Common research questions and patterns
        
        Returns:
            List of dictionaries containing example queries with descriptions and categories
        """
        examples = [
            # Basic gene and lifespan queries with proper ordering
            {
                "category": "Lifespan Effects - Ordered by Magnitude",
                "description": "Genes that increase lifespan, ordered by greatest extension first",
                "query": "SELECT HGNC, model_organism, effect_on_lifespan, lifespan_percent_change_mean FROM lifespan_change WHERE effect_on_lifespan = 'increases lifespan' AND lifespan_percent_change_mean IS NOT NULL ORDER BY lifespan_percent_change_mean DESC",
                "key_concept": "Always order lifespan results by magnitude for relevance. Use LIMIT only when user specifically asks for 'top N' or similar"
            },
            {
                "category": "Lifespan Effects - Ordered by Magnitude", 
                "description": "Genes that decrease lifespan, ordered by greatest reduction first",
                "query": "SELECT HGNC, model_organism, effect_on_lifespan, lifespan_percent_change_mean FROM lifespan_change WHERE effect_on_lifespan = 'decreases lifespan' AND lifespan_percent_change_mean IS NOT NULL ORDER BY lifespan_percent_change_mean ASC",
                "key_concept": "Use ASC ordering for lifespan reductions to show largest decreases first. Use LIMIT only when user specifically asks for 'top N' or similar"
            },
            {
                "category": "Lifespan Effects - Mean vs Maximum",
                "description": "Show both mean and maximum lifespan changes when user asks about lifespan effects",
                "query": "SELECT HGNC, model_organism, effect_on_lifespan, lifespan_percent_change_mean, lifespan_percent_change_max, significance_mean, significance_max FROM lifespan_change WHERE effect_on_lifespan = 'increases lifespan' AND (lifespan_percent_change_mean IS NOT NULL OR lifespan_percent_change_max IS NOT NULL) ORDER BY COALESCE(lifespan_percent_change_mean, lifespan_percent_change_max) DESC",
                "key_concept": "IMPORTANT: When user asks about lifespan effects without specifying mean vs max, show both metrics. Researchers may be interested in either average effects or maximum potential. Use LIMIT only when user specifically asks for 'top N' or similar"
            },
            {
                "category": "Lifespan Effects - Mean vs Maximum",
                "description": "Compare mean vs maximum lifespan changes for the same interventions",
                "query": "SELECT HGNC, model_organism, lifespan_percent_change_mean, lifespan_percent_change_max, (lifespan_percent_change_max - lifespan_percent_change_mean) as max_vs_mean_diff FROM lifespan_change WHERE lifespan_percent_change_mean IS NOT NULL AND lifespan_percent_change_max IS NOT NULL AND effect_on_lifespan = 'increases lifespan' ORDER BY max_vs_mean_diff DESC",
                "key_concept": "Show the difference between maximum and mean effects to highlight variability in responses. Use LIMIT only when user specifically asks for 'top N' or similar"
            },
            
            # Multi-value field queries (CRITICAL pattern)
            {
                "category": "Multi-Value Fields - LIKE Queries",
                "description": "Find genes associated with stem cell exhaustion hallmark",
                "query": "SELECT HGNC, \"hallmarks of aging\" FROM gene_hallmarks WHERE \"hallmarks of aging\" LIKE '%stem cell exhaustion%'",
                "key_concept": "CRITICAL: Always use LIKE with wildcards for multi-value fields"
            },
            {
                "category": "Multi-Value Fields - LIKE Queries",
                "description": "Find interventions that improve cardiovascular system",
                "query": "SELECT HGNC, intervention_improves, effect_on_lifespan, lifespan_percent_change_mean FROM lifespan_change WHERE intervention_improves LIKE '%cardiovascular system%' ORDER BY lifespan_percent_change_mean DESC",
                "key_concept": "Use LIKE queries for intervention_improves and intervention_deteriorates columns"
            },
            {
                "category": "Multi-Value Fields - LIKE Queries",
                "description": "Find genes affecting mitochondrial function",
                "query": "SELECT HGNC, \"hallmarks of aging\" FROM gene_hallmarks WHERE \"hallmarks of aging\" LIKE '%mitochondrial%'",
                "key_concept": "Multi-value hallmarks field requires LIKE pattern matching"
            },
            
            # Cross-table analysis
            {
                "category": "Cross-Table Analysis",
                "description": "Genes with both experimental lifespan effects and population longevity associations",
                "query": "SELECT DISTINCT lc.HGNC, lc.effect_on_lifespan, lc.model_organism, la.ethnicity, la.\"study type\" FROM lifespan_change lc INNER JOIN longevity_associations la ON lc.HGNC = la.HGNC WHERE lc.effect_on_lifespan = 'increases lifespan'",
                "key_concept": "Join tables using HGNC to combine experimental and population data"
            },
            {
                "category": "Cross-Table Analysis - CRITICAL PATTERN",
                "description": "COMPREHENSIVE AGING EVIDENCE: For questions asking about 'evidence of gene X and aging', ALWAYS query ALL 4 tables",
                "query": "SELECT criteria FROM gene_criteria WHERE HGNC = 'PTEN'",
                "key_concept": "CRITICAL: For comprehensive aging evidence questions (like 'What evidence of the link between X and aging'), you MUST query ALL 4 tables: 1) gene_criteria 2) gene_hallmarks 3) lifespan_change 4) longevity_associations. The longevity_associations table contains crucial human population study data that must be included."
            },
            {
                "category": "Cross-Table Analysis - HUMAN POPULATION DATA",
                "description": "ALWAYS include human longevity associations when asked about aging evidence",
                "query": "SELECT \"polymorphism id\", \"nucleotide substitution\", \"amino acid substitution\", ethnicity, \"study type\" FROM longevity_associations WHERE HGNC = 'PTEN'",
                "key_concept": "ESSENTIAL: When user asks for aging evidence of a gene, human population studies from longevity_associations table are a key component. Include polymorphism details, ethnicity, and study type."
            },
            {
                "category": "Cross-Table Analysis",
                "description": "Genes with lifespan effects and their aging criteria",
                "query": "SELECT lc.HGNC, lc.effect_on_lifespan, lc.lifespan_percent_change_mean, gc.criteria FROM lifespan_change lc INNER JOIN gene_criteria gc ON lc.HGNC = gc.HGNC WHERE lc.lifespan_percent_change_mean > 20 ORDER BY lc.lifespan_percent_change_mean DESC",
                "key_concept": "Combine lifespan data with criteria to understand gene classifications"
            },
            
            # Organism-specific patterns
            {
                "category": "Model Organism Studies",
                "description": "Compare gene effects across mammals vs non-mammals",
                "query": "SELECT HGNC, model_organism, effect_on_lifespan, lifespan_percent_change_mean FROM lifespan_change WHERE HGNC IN (SELECT HGNC FROM lifespan_change WHERE model_organism IN ('mouse', 'rat', 'rabbit', 'hamster')) AND HGNC IN (SELECT HGNC FROM lifespan_change WHERE model_organism IN ('roundworm Caenorhabditis elegans', 'fly Drosophila melanogaster', 'yeasts')) ORDER BY HGNC, model_organism",
                "key_concept": "Use subqueries to find genes studied in multiple organism types"
            },
            {
                "category": "Model Organism Studies",
                "description": "Mouse studies with a significant mean or maximum lifespan change",
                "query": (
                    "SELECT HGNC, effect_on_lifespan, lifespan_percent_change_mean, "
                    "lifespan_percent_change_max, significance_mean, significance_max "
                    "FROM lifespan_change WHERE model_organism = 'mouse' AND ("
                    "(significance_mean = 1 AND lifespan_percent_change_mean IS NOT NULL) OR "
                    "(significance_max = 1 AND lifespan_percent_change_max IS NOT NULL)) "
                    "ORDER BY MAX("
                    "COALESCE(CASE WHEN significance_mean = 1 THEN ABS(lifespan_percent_change_mean) END, 0), "
                    "COALESCE(CASE WHEN significance_max = 1 THEN ABS(lifespan_percent_change_max) END, 0)"
                    ") DESC"
                ),
                "key_concept": "Show both metrics with their own significance flags. Rank by the largest absolute significant change."
            },
            
            # Intervention and method analysis
            {
                "category": "Intervention Methods",
                "description": "Compare knockout vs overexpression effects on both mean and maximum lifespan",
                "query": "SELECT intervention_method, effect_on_lifespan, COUNT(*) as count, AVG(lifespan_percent_change_mean) as avg_mean_change, AVG(lifespan_percent_change_max) as avg_max_change FROM lifespan_change WHERE intervention_method IN ('gene knockout', 'additional copies of a gene in the genome') AND (lifespan_percent_change_mean IS NOT NULL OR lifespan_percent_change_max IS NOT NULL) GROUP BY intervention_method, effect_on_lifespan ORDER BY intervention_method, avg_mean_change DESC",
                "key_concept": "Group by intervention method and show both mean and maximum lifespan metrics to compare approaches comprehensively"
            },
            
            # Population genetics patterns
            {
                "category": "Population Genetics",
                "description": "Longevity associations by ethnicity and study type",
                "query": "SELECT ethnicity, \"study type\", COUNT(*) as association_count FROM longevity_associations WHERE ethnicity != 'n/a' GROUP BY ethnicity, \"study type\" ORDER BY association_count DESC",
                "key_concept": "Analyze population genetics patterns across ethnicities. No LIMIT needed for aggregate statistics"
            },
            {
                "category": "Population Genetics",
                "description": "ALL polymorphisms for specific genes (no LIMIT when user asks about gene polymorphisms)",
                "query": "SELECT HGNC, \"polymorphism type\", \"polymorphism id\", \"nucleotide substitution\", ethnicity, \"study type\" FROM longevity_associations WHERE HGNC = 'FOXO3'",
                "key_concept": "When user asks about polymorphisms in a gene, show ALL entries without LIMIT to provide complete information"
            },
            {
                "category": "Population Genetics - When to use LIMIT",
                "description": "Top 5 genes with most longevity associations (LIMIT appropriate here)",
                "query": "SELECT HGNC, COUNT(*) as association_count FROM longevity_associations GROUP BY HGNC ORDER BY association_count DESC LIMIT 5",
                "key_concept": "Use LIMIT only when user specifically asks for 'top N' results or similar superlative language"
            },
            
            # Summary and statistical queries
            {
                "category": "Summary Statistics",
                "description": "Top genes by number of experiments across all organisms (use LIMIT only when user asks for 'top N')",
                "query": "SELECT HGNC, COUNT(*) as experiment_count, COUNT(DISTINCT model_organism) as organism_count FROM lifespan_change WHERE HGNC IS NOT NULL GROUP BY HGNC ORDER BY experiment_count DESC LIMIT 10",
                "key_concept": "Count experiments and organisms per gene for research breadth. Use LIMIT only when user specifically asks for 'top N' genes, otherwise show all results"
            },
            {
                "category": "Summary Statistics",
                "description": "Distribution of lifespan effects by organism (including both mean and max metrics)",
                "query": "SELECT model_organism, effect_on_lifespan, COUNT(*) as count, AVG(lifespan_percent_change_mean) as avg_mean_change, AVG(lifespan_percent_change_max) as avg_max_change, COUNT(CASE WHEN lifespan_percent_change_mean IS NOT NULL THEN 1 END) as mean_data_points, COUNT(CASE WHEN lifespan_percent_change_max IS NOT NULL THEN 1 END) as max_data_points FROM lifespan_change GROUP BY model_organism, effect_on_lifespan ORDER BY model_organism, count DESC",
                "key_concept": "Analyze effect distributions across model organisms with both metrics and data availability counts"
            },
            
            # Advanced pattern examples
            {
                "category": "Advanced Patterns",
                "description": "Genes with multiple aging hallmarks (complex multi-value query)",
                "query": "SELECT HGNC, \"hallmarks of aging\", (LENGTH(\"hallmarks of aging\") - LENGTH(REPLACE(\"hallmarks of aging\", ',', '')) + 1) as hallmark_count FROM gene_hallmarks WHERE hallmark_count > 3 ORDER BY hallmark_count DESC",
                "key_concept": "Count comma-separated values in multi-value fields"
            },
            {
                "category": "Advanced Patterns",
                "description": "Genes affecting both lifespan and specific biological processes",
                "query": "SELECT lc.HGNC, lc.effect_on_lifespan, lc.lifespan_percent_change_mean, lc.intervention_improves FROM lifespan_change lc WHERE lc.intervention_improves LIKE '%cardiovascular system%' AND lc.intervention_improves LIKE '%cognitive function%' ORDER BY lc.lifespan_percent_change_mean DESC",
                "key_concept": "Use multiple LIKE conditions to find genes affecting multiple systems"
            }
        ]
        
        return examples

    def _get_known_enumerations(self) -> Dict[str, Dict[str, List[str]]]:
        """Get comprehensive enumerations for database fields from the OpenGenes database."""
        return {
            "lifespan_change": {
                "model_organism": [
                    "mouse", "roundworm Caenorhabditis elegans", "fly Drosophila melanogaster", 
                    "rabbit", "rat", "acyrthosiphon pisum", "yeasts", "fish Nothobranchius furzeri", 
                    "fungus Podospora anserina", "hamster", "zebrafish", "fish Nothobranchius guentheri"
                ],
                "sex": ["male", "female", "all", "hermaphrodites", "not specified", "None"],
                "effect_on_lifespan": [
                    "increases lifespan", "no change", "decreases lifespan", 
                    "increases lifespan in animals with decreased lifespans", 
                    "decreases survival under stress conditions", 
                    "improves survival under stress conditions", 
                    "decreases life span in animals with increased lifespans", 
                    "no change under stress conditions"
                ],
                "main_effect_on_lifespan": ["loss of function", "switch of function", "gain of function"],
                "intervention_way": [
                    "changes in genome level", "combined (inducible mutation)", 
                    "interventions by selective drug/RNAi"
                ],
                "intervention_method": [
                    "gene knockout", "gene modification to affect product activity/stability", 
                    "gene modification", "additional copies of a gene in the genome", 
                    "addition to the genome of a dominant-negative gene variant that reduces the activity of an endogenous protein", 
                    "treatment with vector with additional gene copies", 
                    "gene modification to reduce protein activity/stability", 
                    "interfering RNA transgene", "RNA interferention", 
                    "gene modification to increase protein activity/stability", 
                    "introduction into the genome of a construct under the control of a gene promoter, which causes death or a decrease in the viability of cells expressing the gene", 
                    "knockout of gene isoform", "tissue-specific gene knockout", 
                    "reduced expression of one of the isoforms in transgenic animals", 
                    "gene modification to reduce gene expression", "treatment with gene product inducer", 
                    "None", "tissue-specific gene overexpression", 
                    "additional copies of a gene in transgenic animals", 
                    "treatment with a gene product inhibitor", "treatment with protein", 
                    "gene modification to increase gene expression", "removal of cells expressing the gene", 
                    "splicing modification"
                ],
                "diet": [
                    "standard chow", "None", "Purina Lab Diet 5001, ad libitum", 
                    "Purina Lab Diet 5001, from birth to 12 weeks ad. lib., then 40% from ad. lib.", 
                    "Teklad LM485 Diet, ad libitum", "Teklad 2018S Diet, ad libitum", 
                    "E. coli OP50, NGM", "96W chow ad.lib.", "calorie-restricted diet", 
                    "2% yeast, 10% sucrose, 5% cornmeal", "2% yeasts, 10% sucrose, 5% cornmeal", 
                    "high-calorie food, 15% dextrose, 15% yeast, 2% agar", 
                    "low-calorie food, 5% dextrose, 5% yeast, 2% agar", "ad libitum", 
                    "agar, corn meal, yeast and molasses", "E. coli HT115 L4440, NGM"
                ],
                "tissue": [
                    "None", "muscle", "neurons", "fat body", "dopaminergic neurons", "glia", 
                    "brain", "corpora cardiaca", "insulin-producing cells", "central nervous system", 
                    "intestine", "liver", "heart", "myeloid cells", "intestinal stem cells and enteroblasts", 
                    "adipose tissue", "melanocytes,Trp2 expressing neurons", "cardiomyocytes", 
                    "hepatocytes", "heart,skeletal muscles", "heart,brain,skeletal muscles", 
                    "skin", "eye", "connective tissue", "cholinergic neurons", "kidney,brain", 
                    "kidney,heart,brain", "neurolemma", "hypodermis", "mediobasal hypothalamus", 
                    "motor neurons", "median neurosecretory cells", 
                    "hypocretin expressing neurons in the hypothalamus", "body wall muscles", 
                    "pharynx", "digestive tract", "abdominal fat and the digestive tract", 
                    "skeletal muscles", "white adipose tissue"
                ],
                "drug": [
                    "None", "tamoxifen", "mifepristone RU486", "AAV9-mTERT", "MCMV-TERT", 
                    "interfering RNA expressing bacteries", "auxin", "heat shock", "heat pulse", 
                    "Ex8[Pcdc-48.1::cdc-48.1]", "tetracycline", "EUK-008", "EUK-134", 
                    "interfering RNA expressing bacteries 1:1000", "interfering RNA expressing bacteries 1:50", 
                    "interfering RNA expressing bacteries 1:10", "DL-beta-hydroxybutyrate", 
                    "DL-beta-hydroxybutyrate + sodium butirate", "lentiviruses, expressing DN-IkB-a", 
                    "rapamycin", "AP20187", "quinic acid", "Cdc42 activity-specific inhibitor", 
                    "Rosizlitazone", "lentiviruses expressing constitutively active IKK-betta", 
                    "Ex008[SKN-1 S393A::GFP]", "captopril", "Recombinant mouse serum albumin rMSA", 
                    "doxycycline", "Ethanol", "interferring RNA", "MCMV-FST"
                ]
            },
            "gene_criteria": {
                "criteria": [
                    "Age-related changes in gene expression, methylation or protein activity", 
                    "Age-related changes in gene expression, methylation or protein activity in humans", 
                    "Association of genetic variants and gene expression levels with longevity", 
                    "Regulation of genes associated with aging", 
                    "Changes in gene activity extend non-mammalian lifespan", 
                    "Changes in gene activity protect against age-related impairment", 
                    "Age-related changes in gene expression, methylation or protein activity in non-mammals", 
                    "Changes in gene activity extend mammalian lifespan", 
                    "Changes in gene activity reduce mammalian lifespan", 
                    "Changes in gene activity enhance age-related deterioration", 
                    "Changes in gene activity reduce non-mammalian lifespan", 
                    "Association of the gene with accelerated aging in humans"
                ]
            },
            "gene_hallmarks": {
                "hallmarks_of_aging_note": "This is a multi-value field containing comma-separated values. Always use LIKE queries with wildcards.",
                "available_hallmarks": [
                    "nuclear DNA instability", "telomere attrition", "alterations in histone modifications",
                    "chromatin remodeling", "transcriptional alterations", "alterations in DNA methylation",
                    "degradation of proteolytic systems", "TOR pathway dysregulation", 
                    "INS/IGF-1 pathway dysregulation", "AMPK pathway dysregulation", 
                    "SIRT pathway dysregulation", "impairment of the mitochondrial integrity and biogenesis",
                    "mitochondrial DNA instability", "accumulation of reactive oxygen species", 
                    "senescent cells accumulation", "stem cell exhaustion", "sterile inflammation", 
                    "intercellular communication impairment", "changes in the extracellular matrix structure", 
                    "impairment of proteins folding and stability", "nuclear architecture impairment", 
                    "disabled macroautophagy"
                ]
            },
            "longevity_associations": {
                "polymorphism_type": ["SNP", "In/Del", "n/a", "haplotype", "VNTR", "PCR-RFLP"],
                "ethnicity": [
                    "Caucasian, American", "European", "Greek", "Ashkenazi Jewish", "Polish", 
                    "Chinese", "Caucasian", "Italian", "Japanese", "Danish", "Spanish", "German", 
                    "European, East Asian, African American", "n/a", "Chinese, Han", "Italian, Southern", 
                    "German, American", "Caucasian, African-American", "East Asian, Europeans, Caucasian American", 
                    "Japanese American", "Italian, Calabrian", "Korean", "Belarusian", "mixed", 
                    "Caucasian, Ashkenazi Jewish", "Dutch", "Amish", "French", 
                    "Ashkenazi Jewish, Amish, Caucasian", "Japanese, Okinawan", "North-eastern Italian", 
                    "Tatars", "American, Caucasians; Italian, Southern; French; Ashkenazi Jewish", 
                    "Chinese, Bama Yao, Guangxi Province", "Swiss", "German, Danes, French", 
                    "American, Caucasian", "Italian, Central", "Finnish"
                ],
                "study type": [
                    "GWAS", "iGWAS", "candidate genes study", "gene-based association approach", 
                    "family study", "single-variant association approach", 
                    "meta-analysis of GWAS, replication of previous findings", "meta-analysis of GWAS", 
                    "GWAS, discovery + replication", "GWAS, replication", "meta-analysis of GWAS, replication", 
                    "n/a", "meta-analysis of candidate gene studies", "immunochip, discovery + replication", 
                    "immunochip"
                ],
                "sex": ["all", "male", "not specified", "female"]
            },
            "biological_processes_for_intervention_effects": {
                "note": "These tags are used in intervention_deteriorates and intervention_improves columns (multi-value fields)",
                "processes": [
                    "cardiovascular system", "nervous system", "immune function", "muscle, bone, skin, liver",
                    "renal function, reproductive function", "cognitive function, eyesight, hair/coat",
                    "body composition", "glucose metabolism, lipid metabolism, cholesterol metabolism",
                    "insulin sensitivity", "oxidation/antioxidant function, mitochondrial function",
                    "DNA metabolism, carcinogenesis, apoptosis", "senescence, inflammation, stress responce",
                    "autophagy, proliferation, locomotor function", "tissue regeneration, stem and progenitor cells",
                    "blood, proteostasis, angiogenesis, metabolism", "endocrine system, intercellular matrix",
                    "building and protection of telomeres", "cytoskeleton organization, nucleus structure",
                    "skin and the intestine epithelial barriers function", "calcium homeostasis, proteolysis"
                ]
            }
        }

# Initialize the OpenGenes MCP server (which inherits from FastMCP)
mcp = OpenGenesMCP()

# Create typer app
app = typer.Typer(help="OpenGenes MCP Server - Database query interface for OpenGenes aging research data")

@app.command("run")
def cli_app(
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Host to bind to"),
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Port to bind to"),
    transport: str = typer.Option("streamable-http", "--transport", help="Transport type")
) -> None:
    """Run the MCP server with specified transport."""
    mcp.run(transport=transport, host=host, port=port)

@app.command("stdio")
def cli_app_stdio(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output")
) -> None:
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")

@app.command("sse")
def cli_app_sse(
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Host to bind to"),
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Port to bind to")
) -> None:
    """Run the MCP server with SSE transport."""
    mcp.run(transport="sse", host=host, port=port)

# Standalone CLI functions for direct script access
def cli_app_run() -> None:
    """Standalone function for opengenes-mcp-run script."""
    mcp.run(transport="streamable-http", host=DEFAULT_HOST, port=DEFAULT_PORT)

def cli_app_stdio() -> None:
    """Standalone function for opengenes-mcp-stdio script."""
    mcp.run(transport="stdio")

def cli_app_sse() -> None:
    """Standalone function for opengenes-mcp-sse script."""
    mcp.run(transport="sse", host=DEFAULT_HOST, port=DEFAULT_PORT)

if __name__ == "__main__":
    app()
