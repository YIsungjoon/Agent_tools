from langchain_core.tools import tool

@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers together.
    
    Use this tool when you need to calculate the product of two numbers (e.g., a * b).
    """
    return a * b
