import sys
import logging
from pathlib import Path
import json

# Setup basic logging to console
logging.basicConfig(level=logging.INFO)

# Make sure we can import the pipeline modules
sys.path.append(str(Path(__file__).parent))

from pipeline.ast_parser import ASTParser
from pipeline.vectorizer import Vectorizer

def run_test():
    repo_slug = "test_repo"
    test_file_path = Path("server.py")
    
    print("=== Testing AST Creation ===")
    parser = ASTParser()
    # parse_file returns a NetworkX DiGraph
    graph = parser.parse_file(test_file_path)
    
    nodes = list(graph.nodes(data=True))
    edges = list(graph.edges(data=True))
    
    print(f"Graph created with {len(nodes)} nodes and {len(edges)} edges.")
    print("Example AST Nodes (first 3):")
    for n in nodes[:3]:
        print(f" - {n[0]}: {n[1].get('kind')} (lines {n[1].get('line_start')}-{n[1].get('line_end')})")
        
    print("\n=== Testing Vector Creation ===")
    print("Initializing ChromaDB Vectorizer...")
    v = Vectorizer(repo_slug)
    
    # We pass the graph_nodes to the single-file method
    # Convert graph edges to what index_file expects conceptually
    # (actually index_repo takes the full graph)
    print("Indexing the file into ChromaDB chunks based on the AST boundaries...")
    chunks_indexed = v.index_repo(repo_path=Path("."), graph=graph, changed_files=["server.py"])
    
    print(f"Successfully chunked and vectorized {chunks_indexed} sections of the file into ChromaDB.")
    
    print("\nTesting Semantic Search:")
    results = v.search("MCP server entrypoint", n_results=1, collection="code")
    if results:
        print(f"Found match! Score: {results[0]['score']}")
        print(f"Matched Node Data:")
        print(json.dumps(results[0]['metadata'], indent=2))
    else:
        print("No results found.")

if __name__ == "__main__":
    run_test()
