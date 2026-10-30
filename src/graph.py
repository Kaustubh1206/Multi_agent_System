from langgraph.graph import START, END , StateGraph



# Langgraph 

builder=StateGraph(str)

# add nodes to our graph
builder.add_node("greeting",greeting)

# add edges
builder.add_edge(START,"greeting")
builder.add_edge("greeting",END)


graph = builder.compile()
