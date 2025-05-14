# https://python.langchain.com/docs/how_to/migrate_agent/
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Sequence
from pydantic import BaseModel
import uvicorn
import operator
import traceback
from langchain.tools.base import StructuredTool
import functools
from langgraph.graph.message import add_messages
import os
from mcp import ClientSession
from langfuse.callback import CallbackHandler
import asyncio
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langchain.tools import Tool

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any

from langgraph.graph import StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import tools_condition, ToolNode


from langgraph.graph import MessagesState
from langchain_core.messages import SystemMessage
import base64
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate

from langgraph.checkpoint.memory import MemorySaver



class State(TypedDict):
    messages: Annotated[list, add_messages]



def encode_image(image_path):
    """Encode image to base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
  
  

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
 
vision_model = ChatOpenAI(model="vllm-llama-3.2-vision", temperature=0.1, api_key=model_key, base_url=api_gateway_url)
rules_model = ChatOpenAI(model="qwen3-vllm", temperature=0.1,  api_key=model_key, base_url=api_gateway_url)  #ChatOpenAI(model="qwen3-vllm", temperature=0.1, max_tokens=5000, api_key=model_key, base_url=api_gateway_url) #ChatOpenAI(model="vllm-llama-3.3", temperature=0.1, api_key=model_key, base_url=api_gateway_url) #ChatOpenAI(model="qwen3-vllm", temperature=0.1,  api_key=model_key, base_url=api_gateway_url, model_kwargs={"remove_thinking": True})  #ChatOpenAI(model="qwen3-vllm", temperature=0.1, max_tokens=5000, api_key=model_key, base_url=api_gateway_url)
supervisor_model = ChatOpenAI(model="vllm-llama-3.3", temperature=0.1, api_key=model_key, base_url=api_gateway_url)


invoice_validation_mcp =     {
        "external_validation_mcp": {
            "url": "http://localhost:5000/sse",  
            "transport": "sse",
        }
    }



invoice_validation_tools = []

document_extraction_agent = None    
invoice_validation_agent = None
rules_agent = None

document_extraction_node = None
invoice_validation_node = None
rules_node = None

workflow = None
memory = None
graph = None


document_extraction_system_prompt = """You are a image extraction agent which converts provided images to json. \n
                                    Your role is to Extract all the fields from the supplied image and provide the information in a structured json format only, with no other text or wrapper around json. \n
                                    The json will be read by machine.  Be very strict about that the output only contains JSON and nothing else. \n
                                    Be very strict about it."""


rules_system_prompt = """You are an expert rule processor which apply the provided ruleset on the given json content. You use the tool calling using the provided MCP tools as needed. \n
                            You will act as per the instructions and rules provided in the user prompt. \n
                            Do not extract any other fields which are not specified in the instrctions or prompt. Be strict about it. \n
                            If any of the rules are not met or failed, then created a additional field in json with the name SUCCESS and set it to false. \n
                            If any external validation is failed using MCP tools, then create a field in json with the name as invalid_data and set it to true. \n
                            If the tool response contains 'Invalid invoice number', this means that valdiation have failed and stop the process immediately."""  

# Define team members
members = {
    "ImageToTextConversionAgent": "An agent using a multi-modal LLM that converts an image and converts into text while extracting fields from the image into a json format.",
    "RulesAgent": "An agent that applies the given ruleset to the json input. You also use tool calling to validate the tax invoice number.",
}

system_prompt = (
    "You are a highly efficient supervisor managing a collaborative conversation between specialized agents:"
    "\n{members_description}"
    "\nYour role is to:"
    "\n1. Analyze the user's request and the ongoing conversation."
    "\n2. Determine which agent is best suited to handle the next task."
    "\n3. Ensure a logical flow of information and task execution."
    "\n4. Correctly detect task completion and respond with 'FINISH', especially when rule agent has been called already. "
    "\n\tIt is criticall that if an agent has been called once, do not call it again. IF it happens just go to next stage or if its the same agent go to FINISH immediately."
    "\n5. Facilitate seamless transitions between agents as needed."
    "\n6. Conclude the process by responding with 'FINISH' when all objectives are met."
    "\nRemember, each agent has unique capabilities, so choose wisely based on the current needs of the task."
)


members_description = "\n".join([f"- {k}: {v}" for k, v in members.items()])

system_prompt = system_prompt.format(members_description=members_description)

# Possible options for the supervisor
options = ["FINISH"] + list(members.keys())

# Define the supervisor's output schema
class RouteResponse(BaseModel):
    """
    The supervisor's response to the user's request.
    """
    next: Literal["FINISH", "ImageToTextConversionAgent", "RulesAgent"]

# Supervisor Prompt
supervisor_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="messages"),
        (
            "system",
            "Based on the conversation, who should act next? Choose one of: {options}",
        ),
    ]
).partial(options=str(options), members=", ".join([f"{k}: {v}" for k, v in members.items()]))


async def supervisor_agent(state):
    print("**************** Supervisor Node ****************")
    supervisor_chain = supervisor_prompt | supervisor_model.with_structured_output(RouteResponse)
    filtered_messages = []
    for message in state["messages"]:
        if isinstance(message, HumanMessage) and isinstance(message.content, list):
            text_content = []
            for item in message.content:
                if isinstance(item, dict) and "type" in item and item["type"] == "text":
                    text_content.append(item)
            if text_content:
                # Create a new message with only text content
                filtered_message = HumanMessage(content=text_content)
                filtered_messages.append(filtered_message)
        else:
            # Keep messages without structured content
            filtered_messages.append(message)
    result = supervisor_chain.invoke({"messages": filtered_messages})
    return {
        "messages": state["messages"] + [AIMessage(content=f"I'll route to {result.next}", name="SupervisorAgent")],
        "next": result.next
    }

    # return supervisor_chain.invoke(state)



class AgentState(TypedDict):
    messages: Annotated[Sequence[HumanMessage], operator.add]
    next: str



# Helper Function for Agent Nodes
    
async def agent_node(state, agent, name):
    filtered_messages = None
    print("**************** Agent Node" + name + " ****************")
    if(name != "ImageToTextConversionAgent"):
        # remove the image_url type content from the messages for non multimodal agents
        filtered_messages = []
        for message in state["messages"]:
            if isinstance(message, HumanMessage) and isinstance(message.content, list):
                text_content = []
                for item in message.content:
                    if isinstance(item, dict) and "type" in item and item["type"] == "text":
                        text_content.append(item)
                if text_content:
                    # Create a new message with only text content
                    filtered_message = HumanMessage(content=text_content)
                    filtered_messages.append(filtered_message)
            else:
                # Keep messages without structured content
                filtered_messages.append(message)
    else:   
        filtered_messages = state["messages"]
        
    
    result = await agent.ainvoke({"messages": filtered_messages})
    # Add the agent's response to the conversation
    return {
        "messages": [AIMessage(content=result["messages"][-1].content, name=name)]
    }



# Helper Function to Process Events
def process_event(event):
    # print(event)
    if "__end__" not in event:
        if "messages" in event:
            agent_name = event["messages"][-1].name
            # content = event["messages"][-1].content
            # print(f"=== {agent_name} ===\n{content}\n")
        elif "next" in event:
            print(f"Supervisor decides the next agent: {event['next']}\n")
    
def print_tools(mcp_tools):
    print("Total tools loaded:", len(mcp_tools))
    for tool in mcp_tools:
        print("Tool:", tool.name)
        print("Tool description:", tool.description)






from fastapi import FastAPI

@asynccontextmanager
async def mcp(app: FastAPI):
    global invoice_validation_tools
    
    try:
        async with MultiServerMCPClient(invoice_validation_mcp) as invoice_validation_client:
            invoice_validation_tools = invoice_validation_client.get_tools()    
            print_tools(invoice_validation_tools)
            yield 

    except Exception as e:
        print(f"Error initializing agent: {str(e)}")
        traceback.print_exc()
        
app = FastAPI(title="LangGraph Agentic Demo with MCP", lifespan=mcp)

@app.get("/api/idp")
async def idp():    
    
    print_tools(invoice_validation_tools)
    document_extraction_agent = create_react_agent(vision_model, tools=[], prompt=document_extraction_system_prompt).with_config({"callbacks": [langfuse_handler], "recursion_limit": 2,})
    rules_agent = create_react_agent(rules_model, tools=invoice_validation_tools, prompt=rules_system_prompt).with_config({"callbacks": [langfuse_handler], "recursion_limit": 10,})

    document_extraction_node = functools.partial(agent_node, agent=document_extraction_agent, name="ImageToTextConversionAgent")
    rules_node = functools.partial(agent_node, agent=rules_agent, name="RulesAgent")

    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("ImageToTextConversionAgent", document_extraction_node)
    workflow.add_node("RulesAgent", rules_node)
    workflow.add_node("Supervisor", supervisor_agent)

    for member in members:
        # Each agent reports back to the supervisor
        workflow.add_edge(member, "Supervisor")

    # Supervisor decides the next agent or to finish
    conditional_map = {member: member for member in members}
    conditional_map["FINISH"] = END
    workflow.add_conditional_edges("Supervisor", lambda x: x["next"], conditional_map)

    # Entry point
    workflow.add_edge(START, "Supervisor")

    # Compile the graph with memory checkpointing
    memory = MemorySaver()
    graph = workflow.compile(checkpointer=memory, debug=False)    
    print(graph.get_graph().print_ascii())
    # Be sure to use different thread_ids for different runs
    import uuid
    thread_id = "fm" #str(uuid.uuid4())    
    config = { "configurable": {"thread_id": thread_id},  
            "callbacks": [langfuse_handler], 
            "recursion_limit": 10,
            "run_name": f"idp_multi-agent_{thread_id}"}




    # Run the graph
    doc = encode_image("/Users/fmamazon/Documents/git/genai-ml-platform-examples/idp-multi-agent-demo/se.png")
    doc_user_prompt = """This is a sales receipt image. Do the following:
                                \n1. Convert the image into text and extract the fields from the image including total amount due, bank account number, tax registered number and invoice number.
                                \n2. Validate the invoice data using the following rules.
                                \n2.1. Make sure that the Bank Account Number field is having atleast 16 characters.
                                \n2.2. Make sure that Invoice date is not in the future.
                                \n2.3. Make sure that Invoice date is not more than 3 months in the past.
                                \n2.4. Check if the total due is not more than 1000. 
                                \n2.5. Validate the tax invoice number using tools and it is not valid, then create a field in json with name as invalid_data and set it to true.
                                \n2.6. If there is an invalid_data field with a value of true, immediately stop the workflow. 
                                \n3. Finish the workflow """

    doc_parser_user_prompt = HumanMessage(content= [
                            {
                                "type": "text",
                                "text": doc_user_prompt,
                                        
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64," + doc
                                }
                            }
                        ])    
    
    
    async for s in graph.astream( {"messages": [doc_parser_user_prompt]}, config=config, stream_mode="values"):
        # print(s)
        print("===============================")        
    # events = graph.astream(
    #     {"messages": [doc_parser_user_prompt]}, 
    #     config=config
    # )

    # for event in events:
    #     process_event(event)
    

if __name__ == "__main__":
    uvicorn.run("idp-flow-integrated-multi-agent:app", host="0.0.0.0", port=8080, reload=True)
