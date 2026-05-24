import os
import json
import sqlite3
import pandas as pd
import networkx as nx
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool

# --- Pydantic Inputs ---

class PreprocessInput(BaseModel):
    file_path: str = Field(description="Path to the messy raw CSV or JSON file.")
    cleaning_instructions: Optional[str] = Field(default=None, description="Optional instruction, e.g., 'drop_na', 'fill_zero', 'remove_duplicates'.")

class RdbInput(BaseModel):
    db_path: str = Field(description="Path to the SQLite database file.")
    action: str = Field(description="Action to perform: 'import' (import data to table), 'query' (run SELECT query), or 'schema' (get table schemas).")
    table_name: Optional[str] = Field(default=None, description="Name of the table (required for import).")
    data_file: Optional[str] = Field(default=None, description="CSV or JSON file path to import (required for import).")
    sql_query: Optional[str] = Field(default=None, description="SQL SELECT query to execute (required for query).")

class GdbInput(BaseModel):
    graph_path: str = Field(description="Path to store/load the local JSON graph database file.")
    action: str = Field(description="Action to perform: 'build' (build graph from nodes/edges data), 'query' (run graph query), or 'schema' (get graph summary).")
    node_data_json: Optional[str] = Field(default=None, description="JSON string listing nodes, e.g. '[{\"id\": \"A\", \"label\": \"Person\"}]'")
    edge_data_json: Optional[str] = Field(default=None, description="JSON string listing edges, e.g. '[{\"from\": \"A\", \"to\": \"B\", \"type\": \"FRIEND\"}]'")
    query_node: Optional[str] = Field(default=None, description="The target node ID to query.")
    query_type: Optional[str] = Field(default="neighbors", description="Query type: 'neighbors' (all connected nodes), 'shortest_path' (path between query_node and target), or 'centrality' (PageRank/Degree).")
    target_node: Optional[str] = Field(default=None, description="The target node ID for 'shortest_path' query.")

# --- Tool 1: Data Preprocessing ---

@tool("preprocess_and_clean_data", args_schema=PreprocessInput)
def preprocess_and_clean_data(file_path: str, cleaning_instructions: str = None) -> str:
    """Preprocesses, cleans, and normalizes inconsistent raw data (CSV/JSON) using pandas.
    
    Handles duplicates, missing values, and structure alignment before storing in RDB or GDB.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' does not exist."
    
    try:
        # Load data
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path)
        elif path.suffix.lower() == ".json":
            df = pd.read_json(path)
        else:
            return f"Error: Unsupported format '{path.suffix}'. Use CSV or JSON."
            
        initial_shape = df.shape
        logs = [f"Initial dataset loaded: {initial_shape[0]} rows, {initial_shape[1]} columns."]
        
        # Apply standard cleaning instructions
        inst = str(cleaning_instructions).lower() if cleaning_instructions else ""
        
        if "remove_duplicates" in inst or not cleaning_instructions:
            df = df.drop_duplicates()
            logs.append(f"Removed duplicates. Row count: {initial_shape[0]} -> {df.shape[0]}.")
            
        if "drop_na" in inst:
            df = df.dropna()
            logs.append(f"Dropped rows with missing values. Row count: {df.shape[0]}.")
        elif "fill_zero" in inst:
            df = df.fillna(0)
            logs.append("Filled missing values with 0.")
        else:
            # Smart default: fill missing text with empty string, numeric with mean
            for col in df.columns:
                if df[col].dtype in ['int64', 'float64']:
                    df[col] = df[col].fillna(df[col].mean())
                else:
                    df[col] = df[col].fillna("")
            logs.append("Smart preprocessing: filled numeric NaNs with column mean, string NaNs with empty text.")

        # Save preprocessed file
        out_path = path.parent / f"cleaned_{path.name}"
        if path.suffix.lower() == ".csv":
            df.to_csv(out_path, index=False)
        else:
            df.to_json(out_path, orient="records", indent=2)
            
        logs.append(f"Cleaned dataset saved successfully to: '{out_path.resolve()}'")
        
        # Format a quick markdown overview of the cleaned dataset
        summary = f"### 📊 Data Preprocessing Summary\n"
        summary += "\n".join([f"- {l}" for l in logs]) + "\n\n"
        summary += "#### Columns Overview:\n"
        for col in df.columns:
            summary += f"- **{col}**: {df[col].dtype} (Unique values: {df[col].nunique()})\n"
            
        return summary
    except Exception as e:
        return f"Error preprocessing data: {str(e)}"

# --- Tool 2: Relational Database (RDB) SQLite Manager ---

@tool("rdb_store_and_query", args_schema=RdbInput)
def rdb_store_and_query(db_path: str, 
                        action: str, 
                        table_name: str = None, 
                        data_file: str = None, 
                        sql_query: str = None) -> str:
    """Relational Database (RDB) manager. Handles SQLite schema design, CSV/JSON import, and safe SQL queries.
    
    Use this when data is highly structured, consistent, and requires traditional tabular SQL relational analysis.
    """
    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    action_clean = action.strip().lower()
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        if action_clean == "import":
            if not table_name or not data_file:
                return "Error: Both 'table_name' and 'data_file' are required for import action."
            
            data_path = Path(data_file)
            if not data_path.exists():
                return f"Error: Data file '{data_file}' does not exist."
                
            # Load and write using pandas
            if data_path.suffix.lower() == ".csv":
                df = pd.read_csv(data_path)
            elif data_path.suffix.lower() == ".json":
                df = pd.read_json(data_path)
            else:
                return "Error: Data file must be CSV or JSON."
                
            df.to_sql(table_name, conn, if_exists="replace", index=False)
            conn.commit()
            
            # Retrieve schema
            cursor.execute(f"PRAGMA table_info({table_name})")
            cols = cursor.fetchall()
            col_desc = ", ".join([f"{c[1]} {c[2]}" for c in cols])
            
            return f"📂 RDB Import SUCCESS!\n- Imported {len(df)} rows into table '{table_name}'.\n- Created Schema: `({col_desc})`"
            
        elif action_clean == "query":
            if not sql_query:
                return "Error: 'sql_query' is required for query action."
                
            # Security filter: restrict write operations
            forbidden = ["drop", "delete", "update", "insert", "alter", "truncate"]
            query_lower = sql_query.lower()
            for word in forbidden:
                if f" {word} " in f" {query_lower} " or query_lower.startswith(word):
                    return f"Security Error: Write operation '{word}' is BLOCKED. Only SELECT read queries are allowed."
            
            df = pd.read_sql_query(sql_query, conn)
            
            # Format as markdown table
            if df.empty:
                return "Query executed successfully. Result set is EMPTY."
                
            # Truncate result rows to prevent context overflow (limit to 50 rows)
            truncated = len(df) > 50
            display_df = df.head(50)
            
            md_table = display_df.to_markdown(index=False)
            output = f"🔍 SQL Query Output ({min(len(df), 50)} of {len(df)} rows displayed):\n\n{md_table}"
            if truncated:
                output += "\n\n*Warning: Output truncated to first 50 rows.*"
            return output
            
        elif action_clean == "schema":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            if not tables:
                return "RDB is empty. No tables found."
                
            schema_out = "🗄️ Relational Database Schemas:\n"
            for t in tables:
                t_name = t[0]
                cursor.execute(f"PRAGMA table_info({t_name})")
                cols = cursor.fetchall()
                col_desc = "\n".join([f"  - {c[1]} ({c[2]})" for c in cols])
                schema_out += f"\n### Table: `{t_name}`\n{col_desc}\n"
            return schema_out
            
        else:
            return f"Error: Unknown RDB action '{action}'."
            
    except Exception as e:
        return f"RDB Execution Error: {str(e)}"
    finally:
        conn.close()

# --- Tool 3: Graph Database (GDB) NetworkX Manager ---

@tool("gdb_store_and_query", args_schema=GdbInput)
def gdb_store_and_query(graph_path: str, 
                        action: str, 
                        node_data_json: str = None, 
                        edge_data_json: str = None, 
                        query_node: str = None, 
                        query_type: str = "neighbors", 
                        target_node: str = None) -> str:
    """Graph Database (GDB) manager. Handles structural network creation, pathfinding, and relationship analysis.
    
    Use this when data is highly interconnected, non-tabular, represents networks, dependencies, or parent-child flows.
    """
    g_path = Path(graph_path)
    g_path.parent.mkdir(parents=True, exist_ok=True)
    
    action_clean = action.strip().lower()
    
    # Load existing graph or create directed graph
    G = nx.DiGraph()
    if g_path.exists():
        try:
            with open(g_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                G = nx.node_link_graph(data)
        except Exception as e:
            logger.warning(f"Failed to load existing graph file: {e}. Creating new graph.")
            
    try:
        if action_clean == "build":
            nodes_added = 0
            edges_added = 0
            
            if node_data_json:
                nodes = json.loads(node_data_json)
                for n in nodes:
                    n_id = str(n.get("id"))
                    n_attr = {k: v for k, v in n.items() if k != "id"}
                    G.add_node(n_id, **n_attr)
                    nodes_added += 1
                    
            if edge_data_json:
                edges = json.loads(edge_data_json)
                for e in edges:
                    u = str(e.get("from", e.get("from_node", e.get("source"))))
                    v = str(e.get("to", e.get("target")))
                    e_attr = {k: v for k, v in e.items() if k not in ["from", "from_node", "to", "target", "source"]}
                    G.add_edge(u, v, **e_attr)
                    edges_added += 1
                    
            # Save graph back to disk
            with open(g_path, "w", encoding="utf-8") as f:
                json.dump(nx.node_link_data(G), f, indent=2)
                
            return f"🕸️ GDB Build SUCCESS!\n- Imported {nodes_added} nodes, {edges_added} edges.\n- Graph currently has {G.number_of_nodes()} total nodes and {G.number_of_edges()} total edges."
            
        elif action_clean == "query":
            q_type = str(query_type).lower().strip()
            
            if q_type == "neighbors":
                if not query_node:
                    return "Error: 'query_node' ID is required for neighbors query."
                if not G.has_node(str(query_node)):
                    return f"Error: Node '{query_node}' does not exist in the graph database."
                
                n_id = str(query_node)
                attr = G.nodes[n_id]
                
                # Incoming and Outgoing neighbors
                predecessors = list(G.predecessors(n_id))
                successors = list(G.successors(n_id))
                
                output = f"🕸️ Graph Neighbors Query: `{n_id}`\n"
                output += f"- **Node Attributes**: `{json.dumps(attr, ensure_ascii=False)}`\n"
                output += f"- **Predecessors (Incoming Connections)**: {predecessors}\n"
                output += f"- **Successors (Outgoing Connections)**: {successors}\n\n"
                
                if successors:
                    output += "#### Outgoing Edges Details:\n"
                    for s in successors:
                        edge_data = G.get_edge_data(n_id, s)
                        output += f"  - `{n_id}` --[{edge_data.get('type', 'RELATES_TO')}]--> `{s}` (Properties: `{json.dumps(edge_data, ensure_ascii=False)}`)\n"
                return output
                
            elif q_type == "shortest_path":
                if not query_node or not target_node:
                    return "Error: Both 'query_node' and 'target_node' IDs are required for shortest_path query."
                u, v = str(query_node), str(target_node)
                if not G.has_node(u) or not G.has_node(v):
                    return f"Error: Nodes '{u}' or '{v}' do not exist in the graph."
                
                try:
                    path = nx.shortest_path(G, source=u, target=v)
                    path_str = " ➔ ".join([f"`{node}`" for node in path])
                    
                    # Compute total edge weights/types if present
                    steps = []
                    for i in range(len(path) - 1):
                        edge_data = G.get_edge_data(path[i], path[i+1])
                        steps.append(f"  - `{path[i]}` --[{edge_data.get('type', 'RELATES_TO')}]--> `{path[i+1]}`")
                        
                    output = f"🔗 Shortest Relationship Path: {path_str}\n\n"
                    output += "#### Step-by-Step Connections:\n" + "\n".join(steps)
                    return output
                except nx.NetworkXNoPath:
                    return f"No path exists between node '{u}' and node '{v}'."
                    
            elif q_type == "centrality":
                if G.number_of_nodes() == 0:
                    return "Graph is empty. Cannot compute centrality."
                pagerank = nx.pagerank(G, alpha=0.85)
                degree = nx.degree_centrality(G)
                
                # Sort and get top 5
                top_pr = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:5]
                top_deg = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:5]
                
                output = "📈 Graph Importance & Influence Report (Top 5 Nodes):\n\n"
                output += "#### 👑 PageRank Centrality (Structural Influence):\n"
                for node, score in top_pr:
                    output += f"  - `{node}`: {score:.4f}\n"
                output += "\n#### 🔗 Degree Centrality (Connection Density):\n"
                for node, score in top_deg:
                    output += f"  - `{node}`: {score:.4f}\n"
                return output
            else:
                return f"Error: Unknown graph query type '{query_type}'."
                
        elif action_clean == "schema":
            if G.number_of_nodes() == 0:
                return "Graph Database is empty."
            
            density = nx.density(G)
            summary = f"🕸️ Graph Database Schema Summary:\n"
            summary += f"- **Graph Type**: Directed Network Graph\n"
            summary += f"- **Number of Nodes**: {G.number_of_nodes()}\n"
            summary += f"- **Number of Edges**: {G.number_of_edges()}\n"
            summary += f"- **Network Connection Density**: {density:.4f}\n"
            summary += f"- **Graph Nodes List**: {list(G.nodes.keys())[:15]}"
            if G.number_of_nodes() > 15:
                summary += "... (and more)"
            return summary
        else:
            return f"Error: Unknown GDB action '{action}'."
            
    except Exception as e:
        return f"GDB Execution Error: {str(e)}"
