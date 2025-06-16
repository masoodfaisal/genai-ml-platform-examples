# https://python.langchain.com/docs/how_to/migrate_agent/
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
import os
from mcp import ClientSession
from langfuse.callback import CallbackHandler
import asyncio
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage
from langfuse.callback import CallbackHandler




model_key="sk-fce2XjCQCitvSv0DJiru1w"
api_gateway_url="http://llama-cpp-cpu-lb-656392498.ap-southeast-2.elb.amazonaws.com"

langfuse_url="http://langfuse-lb-730705963.ap-southeast-2.elb.amazonaws.com"
local_public_key = "pk-lf-ad058c9d-da05-4814-9011-ab0197b2b41e"
local_secret_key = "sk-lf-f09f4cfa-8ede-4ff3-ab1f-f1ed0d2299a4" 

os.environ["LANGFUSE_SECRET_KEY"] = local_secret_key
os.environ["LANGFUSE_HOST"] = langfuse_url
os.environ["LANGFUSE_PUBLIC_KEY"] = local_public_key

 
# Initialize Langfuse CallbackHandler for Langchain (tracing)
langfuse_handler = CallbackHandler()


# Configure LLM
llm_model = "qwen3-vllm"

model = ChatOpenAI( model=llm_model, temperature=0, 
                   api_key=model_key, base_url=api_gateway_url)




fruit_server_params =     {
        "fruit_price_services": {
            "url": "http://localhost:8000/sse",  # If already running
            "transport": "sse",
        }
    }

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate






app = FastAPI(title="LangGraph Agentic Demo with MCP")


@app.post("/api/fruits")
async def q():

    async with MultiServerMCPClient(fruit_server_params) as client:
        graph =create_react_agent(model, client.get_tools(), debug=True)
        # graph.get_graph().draw_mermaid_png(output_file_path="fruit-flow.png")
        graph = graph.with_config({
                "run_name": "fruit_agent",
                "callbacks": [langfuse_handler],
                "recursion_limit": 5,
            })        
        
        inputs = {"messages": [("user", "Calculate the price in dollars for 10 kg of oranges? Use the tool calling to get the price of oranges")]}
        async for s in graph.astream(inputs, stream_mode="values"):
            message = s["messages"][-1]
            if isinstance(message, tuple):
                print(message)
            else:
                message.pretty_print()
                
            if isinstance(message, AIMessage):
                final_message = message.content
                print("Final message:", final_message)                
        return final_message
        

                




if __name__ == "__main__":
    uvicorn.run("react-agent-demo:app", host="0.0.0.0", port=8080, reload=True)
    
    
