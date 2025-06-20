import json
import pytest
import os
from pathlib import Path

from dotenv import load_dotenv
from just_agents import llm_options
from just_agents.base_agent import BaseAgent
from opengenes_mcp.server import OpenGenesMCP

# Load environment
load_dotenv(override=True)

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TEST_DIR = PROJECT_ROOT / "test"

# Load judge prompt
with open(TEST_DIR / "judge_prompt.txt", "r", encoding="utf-8") as f:
    JUDGE_PROMPT = f.read().strip()

# Load system prompt for test agent from Hugging Face
from opengenes_mcp.server import get_prompt_content
SYSTEM_PROMPT = get_prompt_content().strip() + "\n\nIn your response, include the SQL query that you used to answer the question."

# Load reference Q&A data
with open(TEST_DIR / "test_qa.json", "r", encoding="utf-8") as f:
    QA_DATA = json.load(f)


answers_model = {
    "model": "gemini/gemini-2.5-flash-preview-05-20",
    "temperature": 0.0
}

judge_model = {
    "model": "gemini/gemini-2.5-flash-preview-05-20",
    "temperature": 0.0
}

# Initialize agents
opengenes_server = OpenGenesMCP()
test_agent = BaseAgent(
    llm_options=answers_model,
    tools=[opengenes_server.db_query, opengenes_server.get_schema_info, opengenes_server.get_example_queries],
    system_prompt=SYSTEM_PROMPT
)
judge_agent = BaseAgent(
    llm_options=judge_model,
    tools=[],
    system_prompt=JUDGE_PROMPT
)

@pytest.mark.skipif(
    os.getenv("CI") in ("true", "1", "True") or 
    os.getenv("GITHUB_ACTIONS") in ("true", "1", "True") or 
    os.getenv("GITLAB_CI") in ("true", "1", "True") or 
    os.getenv("JENKINS_URL") is not None,
    reason="Skipping expensive LLM tests in CI to save costs. Run locally with: pytest test/test_judge.py"
)
@pytest.mark.parametrize("qa_item", QA_DATA, ids=[f"Q{i+1}" for i in range(len(QA_DATA))])
def test_question_with_judge(qa_item):
    """Test each question by generating an answer and evaluating it with the judge."""
    question = qa_item["question"]
    reference_answer = qa_item["answer"]
    reference_sql = qa_item.get("sql", "")
    
    # Generate answer
    generated_answer = test_agent.query(question)
    
    # Judge evaluation
    judge_input = f"""
QUESTION: {question}

REFERENCE ANSWER: {reference_answer}

REFERENCE SQL: {reference_sql}

GENERATED ANSWER: {generated_answer}
"""
    
    judge_result = judge_agent.query(judge_input).strip().upper()
    
    # Print for debugging
    print(f"\nQuestion: {question}")
    print(f"Generated: {generated_answer[:200]}...")
    print(f"Judge: {judge_result}")
    
    if "PASS" not in judge_result:
        print(f"\n=== JUDGE FAILED ===")
        print(f"Question: {question}")
        print(f"Reference Answer: {reference_answer}")
        print(f"Current Answer: {generated_answer}")
        print(f"===================")
    
    assert "PASS" in judge_result, f"Judge failed for question: {question}" 