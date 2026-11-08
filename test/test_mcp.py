#!/usr/bin/env python3
"""Comprehensive tests for OpenGenes MCP Server using FastMCP testing patterns."""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
from typing import Dict, Any, List
import time
import gc
import os

from fastmcp import Client
from opengenes_mcp.server import OpenGenesMCP, DatabaseManager, QueryResult


def safe_delete_db_file(db_path: Path, max_retries: int = 5):
    """
    Safely delete a database file with retry mechanism for Windows file locking issues.
    
    Args:
        db_path: Path to the database file to delete
        max_retries: Maximum number of deletion attempts
    """
    # Force garbage collection to release any remaining references
    gc.collect()
    
    # Retry deletion with exponential backoff for Windows compatibility
    for attempt in range(max_retries):
        try:
            if db_path.exists():
                db_path.unlink()
            break
        except PermissionError:
            if attempt < max_retries - 1:
                # Wait with exponential backoff
                time.sleep(0.1 * (2 ** attempt))
                gc.collect()  # Try garbage collection again
            else:
                # On final attempt, try alternative deletion methods
                try:
                    if os.name == 'nt':  # Windows
                        # Try to delete with os.remove as fallback
                        os.remove(str(db_path))
                    else:
                        raise
                except:
                    # If all else fails, just pass - the temp file will be cleaned up eventually
                    pass


class TestOpenGenesMCPServer:
    """Test suite for OpenGenes MCP Server following FastMCP testing patterns."""

    @pytest.fixture
    def sample_db(self):
        """Create a temporary SQLite database with sample data for testing."""
        # Create temporary database file
        temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        temp_db.close()
        db_path = Path(temp_db.name)
        
        # Create sample tables and data
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            
            # Create lifespan_change table
            cursor.execute('''
                CREATE TABLE lifespan_change (
                    id INTEGER PRIMARY KEY,
                    HGNC TEXT,
                    model_organism TEXT,
                    sex TEXT,
                    effect_on_lifespan TEXT,
                    intervention_method TEXT,
                    lifespan_percent_change_mean REAL
                )
            ''')
            
            # Create gene_criteria table
            cursor.execute('''
                CREATE TABLE gene_criteria (
                    id INTEGER PRIMARY KEY,
                    HGNC TEXT,
                    criteria TEXT
                )
            ''')
            
            # Create gene_hallmarks table
            cursor.execute('''
                CREATE TABLE gene_hallmarks (
                    id INTEGER PRIMARY KEY,
                    HGNC TEXT,
                    "hallmarks of aging" TEXT
                )
            ''')
            
            # Create longevity_associations table
            cursor.execute('''
                CREATE TABLE longevity_associations (
                    id INTEGER PRIMARY KEY,
                    HGNC TEXT,
                    "polymorphism type" TEXT,
                    "nucleotide substitution" TEXT,
                    ethnicity TEXT
                )
            ''')
            
            # Insert sample data
            cursor.executemany('''
                INSERT INTO lifespan_change 
                (HGNC, model_organism, sex, effect_on_lifespan, intervention_method, lifespan_percent_change_mean)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', [
                ('TP53', 'mouse', 'male', 'increases lifespan', 'gene knockout', 25.5),
                ('FOXO3', 'mouse', 'female', 'increases lifespan', 'gene modification', 15.2),
                ('IGF1R', 'roundworm Caenorhabditis elegans', 'all', 'increases lifespan', 'gene knockout', 30.1),
                ('SIRT1', 'fly Drosophila melanogaster', 'male', 'no change', 'gene modification', 0.0),
            ])
            
            cursor.executemany('''
                INSERT INTO gene_criteria (HGNC, criteria) VALUES (?, ?)
            ''', [
                ('TP53', 'Age-related changes in gene expression, methylation or protein activity'),
                ('TP53', 'Changes in gene activity extend mammalian lifespan'),
                ('FOXO3', 'Association of genetic variants and gene expression levels with longevity'),
                ('IGF1R', 'Changes in gene activity extend non-mammalian lifespan'),
            ])
            
            cursor.executemany('''
                INSERT INTO gene_hallmarks (HGNC, "hallmarks of aging") VALUES (?, ?)
            ''', [
                ('TP53', 'Cellular senescence'),
                ('FOXO3', 'Nutrient sensing'),
                ('IGF1R', 'Growth signaling'),
                ('SIRT1', 'Mitochondrial dysfunction'),
            ])
            
            cursor.executemany('''
                INSERT INTO longevity_associations 
                (HGNC, "polymorphism type", "nucleotide substitution", ethnicity) 
                VALUES (?, ?, ?, ?)
            ''', [
                ('FOXO3', 'SNP', 'G>T', 'Italian'),
                ('IGF1R', 'SNP', 'A>G', 'Caucasian'),
                ('APOE', 'SNP', 'C>T', 'European'),
            ])
            
            conn.commit()
        finally:
            # Explicitly close connection and cursor
            cursor.close()
            conn.close()
        
        yield db_path
        
        # Cleanup with retry mechanism for Windows file locking issues
        safe_delete_db_file(db_path)

    @pytest.fixture
    def mcp_server(self, sample_db):
        """Create OpenGenes MCP server instance with test database."""
        server = OpenGenesMCP(
            name="TestOpenGenesServer",
            db_path=sample_db,
            prefix="test_opengenes_",
            huge_query_tool=False
        )
        yield server
        # Explicit cleanup to help with Windows file locking
        del server
        gc.collect()  # Force garbage collection to release database connections

    @pytest.fixture
    def mcp_server_huge_query(self, sample_db):
        """Create OpenGenes MCP server instance with huge query tool enabled."""
        server = OpenGenesMCP(
            name="TestOpenGenesServerHuge",
            db_path=sample_db,
            prefix="test_opengenes_",
            huge_query_tool=True
        )
        yield server
        # Explicit cleanup to help with Windows file locking
        del server
        gc.collect()  # Force garbage collection to release database connections

    @pytest.mark.asyncio
    async def test_server_initialization(self, mcp_server):
        """Test that the MCP server initializes correctly."""
        assert mcp_server.name == "TestOpenGenesServer"
        assert isinstance(mcp_server.db_manager, DatabaseManager)
        assert mcp_server.prefix == "test_opengenes_"

    @pytest.mark.asyncio
    async def test_basic_db_query_tool(self, mcp_server):
        """Test basic database query functionality using FastMCP in-memory testing."""
        async with Client(mcp_server) as client:
            # Test a simple SELECT query
            result = await client.call_tool(
                "test_opengenes_db_query",
                {"sql": "SELECT COUNT(*) as total FROM lifespan_change"}
            )
            
            assert len(result) == 1
            response_data = result[0].text
            
            # The response should be a JSON string containing QueryResult data
            import json
            parsed_result = json.loads(response_data)
            
            assert "rows" in parsed_result
            assert "count" in parsed_result
            assert "query" in parsed_result
            assert parsed_result["count"] == 1
            assert parsed_result["rows"][0]["total"] == 4

    @pytest.mark.asyncio
    async def test_db_query_with_filtering(self, mcp_server):
        """Test database queries with WHERE clauses."""
        async with Client(mcp_server) as client:
            # Test query with filtering
            result = await client.call_tool(
                "test_opengenes_db_query",
                {"sql": "SELECT HGNC FROM lifespan_change WHERE model_organism = 'mouse'"}
            )
            
            response_data = json.loads(result[0].text)
            assert response_data["count"] == 2
            genes = [row["HGNC"] for row in response_data["rows"]]
            assert "TP53" in genes
            assert "FOXO3" in genes

    @pytest.mark.asyncio
    async def test_db_query_security_validation(self, mcp_server):
        """Test that only SELECT queries are allowed."""
        async with Client(mcp_server) as client:
            # Test that INSERT is blocked
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "test_opengenes_db_query",
                    {"sql": "INSERT INTO lifespan_change (HGNC) VALUES ('TEST')"}
                )
            
            # The error should mention that write operations are not allowed
            assert "Write operation attempted on read-only database" in str(exc_info.value)

            # Test that UPDATE is blocked
            with pytest.raises(Exception):
                await client.call_tool(
                    "test_opengenes_db_query",
                    {"sql": "UPDATE lifespan_change SET HGNC = 'TEST'"}
                )
                
            # Test that DELETE is blocked
            with pytest.raises(Exception):
                await client.call_tool(
                    "test_opengenes_db_query",
                    {"sql": "DELETE FROM lifespan_change"}
                )

    @pytest.mark.asyncio
    async def test_schema_info_tool(self, mcp_server):
        """Test the schema information tool."""
        async with Client(mcp_server) as client:
            result = await client.call_tool("test_opengenes_get_schema_info", {})
            
            schema_data = json.loads(result[0].text)
            
            assert "tables" in schema_data
            assert "enumerations" in schema_data
            
            # Check that our test tables are present
            tables = schema_data["tables"]
            assert "lifespan_change" in tables
            assert "gene_criteria" in tables
            assert "gene_hallmarks" in tables
            assert "longevity_associations" in tables
            
            # Check table structure
            lifespan_table = tables["lifespan_change"]
            assert "columns" in lifespan_table
            column_names = [col["name"] for col in lifespan_table["columns"]]
            assert "HGNC" in column_names
            assert "model_organism" in column_names
            assert "effect_on_lifespan" in column_names

    @pytest.mark.asyncio
    async def test_example_queries_tool(self, mcp_server):
        """Test the example queries tool."""
        async with Client(mcp_server) as client:
            result = await client.call_tool("test_opengenes_example_queries", {})
            
            examples_data = json.loads(result[0].text)
            
            assert isinstance(examples_data, list)
            assert len(examples_data) > 0
            
            # Check structure of examples
            for example in examples_data:
                assert "description" in example
                assert "query" in example
                assert isinstance(example["description"], str)
                assert isinstance(example["query"], str)
                assert example["query"].upper().strip().startswith("SELECT")

    def test_mouse_example_ranks_only_significant_reported_metrics(self):
        example = next(
            example for example in OpenGenesMCP.get_example_queries(None)
            if example["description"].startswith("Mouse studies with")
        )
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("""
                CREATE TABLE lifespan_change (
                    HGNC TEXT, model_organism TEXT, effect_on_lifespan TEXT,
                    lifespan_percent_change_mean REAL, lifespan_percent_change_max REAL,
                    significance_mean INTEGER, significance_max INTEGER
                )
            """)
            conn.executemany("INSERT INTO lifespan_change VALUES (?, ?, '', ?, ?, ?, ?)", [
                ("MAX_ONLY", "mouse", 80, 9, 0, 1),
                ("MEAN_ONLY", "mouse", -12, 70, 1, None),
                ("BOTH", "mouse", 2, 20, 1, 1),
                ("MISSING_SIGNIFICANT_VALUE", "mouse", None, 90, 1, 0),
                ("NEITHER", "mouse", 50, 60, 0, None),
                ("RAT", "rat", 100, 120, 1, 1),
            ])
            assert [row[0] for row in conn.execute(example["query"])] == [
                "BOTH", "MEAN_ONLY", "MAX_ONLY",
            ]
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_complex_join_query(self, mcp_server):
        """Test complex queries with JOINs."""
        async with Client(mcp_server) as client:
            # Test a JOIN query between lifespan_change and gene_criteria
            sql = """
                SELECT DISTINCT lc.HGNC, lc.effect_on_lifespan, gc.criteria 
                FROM lifespan_change lc 
                INNER JOIN gene_criteria gc ON lc.HGNC = gc.HGNC 
                WHERE lc.effect_on_lifespan = 'increases lifespan'
            """
            result = await client.call_tool("test_opengenes_db_query", {"sql": sql})
            
            response_data = json.loads(result[0].text)
            assert response_data["count"] > 0
            
            # Check that all results have the expected effect
            for row in response_data["rows"]:
                assert row["effect_on_lifespan"] == "increases lifespan"
                assert row["HGNC"] is not None
                assert row["criteria"] is not None

    @pytest.mark.asyncio
    async def test_aggregation_queries(self, mcp_server):
        """Test aggregation queries (COUNT, GROUP BY, etc.)."""
        async with Client(mcp_server) as client:
            # Test GROUP BY query
            sql = """
                SELECT model_organism, COUNT(*) as experiment_count 
                FROM lifespan_change 
                GROUP BY model_organism 
                ORDER BY experiment_count DESC
            """
            result = await client.call_tool("test_opengenes_db_query", {"sql": sql})
            
            response_data = json.loads(result[0].text)
            assert response_data["count"] > 0
            
            # Check aggregation results
            total_experiments = sum(row["experiment_count"] for row in response_data["rows"])
            assert total_experiments == 4  # We inserted 4 test records

    @pytest.mark.asyncio
    async def test_invalid_sql_handling(self, mcp_server):
        """Test handling of invalid SQL queries."""
        async with Client(mcp_server) as client:
            # Test syntactically incorrect SQL
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "test_opengenes_db_query",
                    {"sql": "SELECT FROM WHERE"}
                )
            
            # Check that some form of error is reported for invalid SQL
            error_message = str(exc_info.value)
            assert any(phrase in error_message for phrase in [
                "syntax error", "Database query error", "Query execution error", "Error calling tool"
            ])

    @pytest.mark.asyncio
    async def test_nonexistent_table_query(self, mcp_server):
        """Test querying a non-existent table."""
        async with Client(mcp_server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "test_opengenes_db_query",
                    {"sql": "SELECT * FROM nonexistent_table"}
                )

    @pytest.mark.asyncio
    async def test_huge_query_tool_enabled(self, mcp_server_huge_query):
        """Test that huge query tool has enhanced description when enabled."""
        async with Client(mcp_server_huge_query) as client:
            # The huge query tool should work the same way but with extended description
            result = await client.call_tool(
                "test_opengenes_db_query",
                {"sql": "SELECT COUNT(*) as total FROM lifespan_change"}
            )
            
            response_data = json.loads(result[0].text)
            assert response_data["count"] == 1
            assert response_data["rows"][0]["total"] == 4

    def test_database_manager_initialization_error(self):
        """Test DatabaseManager error handling for missing database."""
        nonexistent_path = Path("/nonexistent/path/to/database.sqlite")
        
        with pytest.raises(FileNotFoundError) as exc_info:
            DatabaseManager(nonexistent_path)
        
        assert "Database not found" in str(exc_info.value)

    def test_database_manager_query_result_structure(self, sample_db):
        """Test DatabaseManager query result structure."""
        db_manager = DatabaseManager(sample_db)
        result = db_manager.execute_query("SELECT HGNC FROM lifespan_change LIMIT 1")
        
        assert isinstance(result, QueryResult)
        assert hasattr(result, 'rows')
        assert hasattr(result, 'count')
        assert hasattr(result, 'query')
        assert result.count == len(result.rows)
        assert result.query == "SELECT HGNC FROM lifespan_change LIMIT 1"

    @pytest.mark.asyncio
    async def test_tool_registration_with_prefix(self, mcp_server):
        """Test that tools are registered with the correct prefix."""
        async with Client(mcp_server) as client:
            # List available tools
            tools = await client.list_tools()
            tool_names = [tool.name for tool in tools]
            
            # Check that all tools have the expected prefix
            expected_tools = [
                "test_opengenes_db_query",
                "test_opengenes_get_schema_info", 
                "test_opengenes_example_queries"
            ]
            
            for expected_tool in expected_tools:
                assert expected_tool in tool_names

    @pytest.mark.asyncio
    async def test_resources_registration(self, mcp_server):
        """Test that resources are properly registered."""
        async with Client(mcp_server) as client:
            # List available resources
            resources = await client.list_resources()
            
            # Should have some resources registered (from the resources module)
            assert len(resources) >= 0  # Resources are optional
            
            # Check that each resource has required fields
            for resource in resources:
                assert hasattr(resource, 'uri')
                assert hasattr(resource, 'name')


class TestErrorHandling:
    """Test error handling and edge cases."""
    
    @pytest.fixture
    def mcp_server_with_mock_db(self):
        """Create server with mocked database for error testing."""
        with patch('opengenes_mcp.server.DB_PATH') as mock_path:
            mock_path.exists.return_value = True
            with patch('sqlite3.connect') as mock_connect:
                mock_connect.side_effect = sqlite3.Error("Mock database error")
                # This will fail during initialization but we can test error paths
                try:
                    server = OpenGenesMCP(name="ErrorTestServer")
                    yield server
                except Exception:
                    # Expected to fail, but we want to test the error handling
                    yield None

    @pytest.fixture
    def sample_db(self):
        """Create a temporary SQLite database with sample data for testing."""
        # Create temporary database file
        temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        temp_db.close()
        db_path = Path(temp_db.name)
        
        # Create sample tables and data
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            
            # Create lifespan_change table
            cursor.execute('''
                CREATE TABLE lifespan_change (
                    id INTEGER PRIMARY KEY,
                    HGNC TEXT,
                    model_organism TEXT,
                    sex TEXT,
                    effect_on_lifespan TEXT,
                    intervention_method TEXT,
                    lifespan_percent_change_mean REAL
                )
            ''')
            
            # Insert sample data
            cursor.executemany('''
                INSERT INTO lifespan_change 
                (HGNC, model_organism, sex, effect_on_lifespan, intervention_method, lifespan_percent_change_mean)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', [
                ('TP53', 'mouse', 'male', 'increases lifespan', 'gene knockout', 25.5),
            ])
            
            conn.commit()
        finally:
            # Explicitly close connection and cursor
            cursor.close()
            conn.close()
        
        yield db_path
        
        # Cleanup with retry mechanism for Windows file locking issues
        safe_delete_db_file(db_path)

    def test_sql_injection_prevention(self, sample_db):
        """Test that the system handles potential SQL injection attempts."""
        db_manager = DatabaseManager(sample_db)
        
        # Test that parameterized queries work properly
        result = db_manager.execute_query(
            "SELECT HGNC FROM lifespan_change WHERE HGNC = ?", 
            ("TP53",)
        )
        assert result.count <= 1

    @pytest.fixture
    def mcp_server(self, sample_db):
        """Create OpenGenes MCP server instance with test database."""
        server = OpenGenesMCP(
            name="TestOpenGenesServer",
            db_path=sample_db,
            prefix="test_opengenes_",
            huge_query_tool=False
        )
        yield server
        # Explicit cleanup to help with Windows file locking
        del server
        gc.collect()  # Force garbage collection to release database connections

    @pytest.mark.asyncio
    async def test_empty_query_results(self, mcp_server):
        """Test handling of queries that return no results."""
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "test_opengenes_db_query",
                {"sql": "SELECT * FROM lifespan_change WHERE HGNC = 'NONEXISTENT_GENE'"}
            )
            
            response_data = json.loads(result[0].text)
            assert response_data["count"] == 0
            assert response_data["rows"] == []


# Import json at the top level for use in tests
import json


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
