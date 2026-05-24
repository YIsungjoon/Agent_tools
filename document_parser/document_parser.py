import os
import csv
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool

class DocumentParseInput(BaseModel):
    file_path: str = Field(description="The absolute or relative path to the document file to parse.")

@tool("parse_document", args_schema=DocumentParseInput)
def parse_document(file_path: str) -> str:
    """Parses local document files (PDF, CSV, TXT, MD, JSON, YAML) and extracts their text content.
    
    Use this tool when you need to read the contents of a local document to find facts, data, or guidelines.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: The file at '{file_path}' does not exist."
        
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            num_pages = len(reader.pages)
            text_parts = []
            # Read up to first 10 pages to avoid context overflow, but enough for core specs
            max_pages = min(num_pages, 10)
            for i in range(max_pages):
                page_text = reader.pages[i].extract_text()
                if page_text.strip():
                    text_parts.append(f"--- Page {i+1} ---\n{page_text}")
            
            summary_info = f"Document Type: PDF\nTotal Pages: {num_pages} (First {max_pages} pages extracted)\n\n"
            return summary_info + "\n".join(text_parts)
            
        elif ext == ".csv":
            rows = []
            with open(path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                # Read up to first 50 rows
                for i, row in enumerate(reader):
                    if i >= 50:
                        break
                    rows.append(",".join(row))
            
            summary_info = f"Document Type: CSV\nTotal Rows Read: {len(rows)}\n\n"
            return summary_info + "\n".join(rows)
            
        elif ext in [".txt", ".md", ".json", ".yaml", ".yml"]:
            with open(path, mode="r", encoding="utf-8") as f:
                content = f.read()
                
            # Truncate to first 10000 characters if extremely long
            truncated = len(content) > 10000
            display_content = content[:10000]
            
            summary_info = f"Document Type: Plain Text ({ext})\nTotal Length: {len(content)} chars\n"
            if truncated:
                summary_info += "Warning: Content truncated to first 10000 characters.\n"
            summary_info += "\n"
            
            return summary_info + display_content
            
        else:
            return f"Error: Unsupported file extension '{ext}'. Only PDF, CSV, TXT, and MD/JSON/YAML are supported."
            
    except Exception as e:
        return f"Error parsing document '{file_path}': {str(e)}"
