import os
import prompt
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from google.cloud import firestore
from langchain_google_firestore import FirestoreChatMessageHistory
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import MessagesPlaceholder, ChatPromptTemplate, PromptTemplate
from langchain_core.messages import BaseMessage, ToolCall, ToolMessage
from langchain_core.agents import AgentAction, AgentFinish
from pydantic import BaseModel, Field
from langchain.tools import BaseTool, StructuredTool, tool, Tool
from langchain.memory import ConversationBufferMemory
from langchain.agents import AgentExecutor, create_structured_chat_agent, create_react_agent, create_tool_calling_agent
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain import hub
from langgraph.graph import StateGraph, END
from typing import Annotated, List, TypedDict, Union
import operator


#load env variables
load_dotenv()

####-------- CONSTANTS/상수 --------####
#For OpenAI ChatGPT
GPT_MODEL = "gpt-4o"
GPT_TEMPERATURE = 0.1 #low temperature reduces possible randomness. #온도를 낮게 설정하여 무작위성을 최소화
MAX_TOKENS = 1000

#For Firebase Firestore
PROJECT_ID = "archie-fff03"
SESSION_ID = "user_session_1"
COLLECTION_NAME = "chat_history"

#for pinecone vector store
PC = Pinecone()
VECTORSTORE_INDEX_NAME = "archie"
VECTORSTORE_INDEX = PC.Index(VECTORSTORE_INDEX_NAME)
print(VECTORSTORE_INDEX.describe_index_stats())

####-------- CONSTANTS/상수 ends --------####


####-------- initialization --------####
print("initializing openAI GPT")
#Init OpenAI model
openAIClient = ChatOpenAI(model = GPT_MODEL, temperature = GPT_TEMPERATURE, max_tokens = MAX_TOKENS)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
print("initializing finished")

print("initializing firestore")
#Init Firebase history storage
firestoreClient = firestore.Client(project = PROJECT_ID)
print("initializing Finished")

#init pincone/vector store 
print("Initializing vectorstore")
db = PineconeVectorStore(index= VECTORSTORE_INDEX, embedding=embeddings)
print("Initializing Finished")

#Init AgentState
print("Initializing Agent")
class AgentState(TypedDict):
    input: str
    chat_history: list[BaseMessage]
    intermediate_steps: Annotated[list[tuple[AgentAction, str]], operator.add]
print("Initializing Finished")

sourceList = []    # list for needed documents (in chunks)
chatHistory = []    # list for chat history

####-------- initialization ends --------####


####-------- DEFINE TOOLS FOR AGENT --------####

@tool("gather_relevant_sources")
def getRelevantSources(
    newInput: Annotated[str, "most recent user input"], 
    chatHistory: Annotated[List, "chat history"] = chatHistory):
    """Saves relevant document according to user input into the local memory. This memory will be used in generate final output"""

    ####--------Creating vector store retriever--------####

    #vector store retriever
    #connected to scientific data vector db
    retriever = db.as_retriever()

    # Contextualize question prompt
    # This system prompt helps the AI understand that it should reformulate the question
    # based on the chat history to make it a standalone question
    contextualizeQSystemPrompt = prompt.contextual_q_system_prompt

    # Create a prompt template for contextualizing questions
    contextualizeQprompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualizeQSystemPrompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )

    # Create a history-aware retrieveri
    # This uses the LLM to help reformulate the question based on chat history
    history_aware_retriever = create_history_aware_retriever(
        openAIClient, retriever, contextualizeQprompt
    )
    ####--------Retriever creation ends--------####

    ####--------Retrieve documents then append--------####
    response = history_aware_retriever.invoke({"input": newInput, "chat_history": chatHistory})

    #append to the answer List
    sourceList.append(response)

    return "Relevant sources are successfully saved in the memory" #파란 텍스트 부분
    ####--------End function--------####

@tool("generate_final_output")
def generateFinalOutput(
    chatHistory: Annotated[List, "chat history"] = chatHistory):
    """generates final output for the user. Used when all the question is asked and answered."""

    #code to generate output 

    finalOutput = sourceList

    return finalOutput

#enforcing formated output. 
@tool("continue_conversation")
def continueConversation(
    reaction: str,
    nextQuestion: str
):
    """Returns a natural language for question progression, those are consist of:
    - `reaction`: a natural reaction to user's previous input to make the interaction feel natural and conversational
    - `nextQuestion`: next question in the conversation according to the prompt. include examples along with the question.
    """

    return ""

#list of the tools used by LLM, will be passed as variables and parameters.
tools = [getRelevantSources, generateFinalOutput, continueConversation]

####-------- DEFINING TOOLS FOR AGENT ENDS --------####



####-------- HELPER FUNCTIONS --------####

def listToString(docList):
    ret = ""
    for doc in docList:
        ret = ret + doc
    
    return ret

def getSourceList():
    return sourceList

####--------HELPER FUNCTION ENDS--------####



####--------Creating decision maker--------####

# define a function to transform intermediate_steps from list
# of AgentAction to scratchpad string
def create_scratchpad(intermediate_steps: list[AgentAction]):
    steps = []
    for i, action in enumerate(intermediate_steps):
        if action.log != "TBD":
            # this was the ToolExecution
            steps.append(
                f"Tool: {action.tool}, input: {action.tool_input}\n"
                f"Output: {action.log}"
            )
    return "\n---\n".join(steps)

# creating prompt template
agentSystemPrompt = ChatPromptTemplate.from_messages(
    [
        ("system", prompt.decisionMakerPrompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        ("assistant", "scratch_pad: {scratch_pad}"),
    ]
)

# creating decision maker
decisionMaker = (
    #input parameters
    {
        "input": lambda x: x["input"],
        "chat_history": lambda x: x["chat_history"],
        "scratch_pad": lambda x: create_scratchpad(
            intermediate_steps=x["intermediate_steps"]
        ),
    }
    #decision maker prompt template
    | agentSystemPrompt
    #prompt and input is passed to the llm that has access to tools and forced to use tools for every invokes (tool_choice="any")
    | openAIClient.bind_tools(tools, tool_choice="any")
)

####--------creating Decision Maker ends--------####



####--------Define Nodes for Graph--------####
# pass the tool use decision to our router which will route the output to the chosen node component to run 
# (we define these below) based on the out.tool_calls[0]["name"] value.

#running decision maker.
def runDecisionMaker(state: list):
    print("run decision maker")
    print(f"intermediate_steps: {state['intermediate_steps']}")
    out = decisionMaker.invoke(state)
    tool_name = out.tool_calls[0]["name"]
    tool_args = out.tool_calls[0]["args"]
    action_out = AgentAction(
        tool=tool_name,
        tool_input=tool_args,
        log="TBD"
    )
    return {
        "intermediate_steps": [action_out]
    }

#decision router
def router(state: list):
    # return the tool name to use
    if isinstance(state["intermediate_steps"], list):
        return state["intermediate_steps"][-1].tool
    else:
        # if we output bad format go to final answer
        print("Router invalid format")
        return "final_answer"

# All of our tools can be run using the same function logic, which we define with run_tool. 
# The input parameters to our tool call and the resultant output are added to our graph state's intermediate_steps parameter.
tool_str_to_func = {
    "gather_relevant_sources": getRelevantSources,
    "generate_final_output": generateFinalOutput,
    "continue_conversation": continueConversation,
}

def run_tool(state: list):
    # use this as helper function so we repeat less code
    tool_name = state["intermediate_steps"][-1].tool
    tool_args = state["intermediate_steps"][-1].tool_input
    print(f"{tool_name}.invoke(input={tool_args})")
    # run tool
    out = tool_str_to_func[tool_name].invoke(input=tool_args)
    action_out = AgentAction(
        tool=tool_name,
        tool_input=tool_args,
        log=str(out)
    )
    return {"intermediate_steps": [action_out]}

####--------Define Nodes for Graph End--------####



####--------Define Graph --------####
graph = StateGraph(AgentState)

graph.add_node("Decision_Maker", runDecisionMaker)
graph.add_node("gather_relevant_sources", run_tool)
graph.add_node("generate_final_output", run_tool)
graph.add_node("continue_conversation", run_tool)

graph.set_entry_point("Decision_Maker")

graph.add_conditional_edges(
    source="Decision_Maker",  # where in graph to start
    path=router,  # function to determine which node is called
)


# create edges from each tool back to the oracle
for tool_obj in tools:
    if tool_obj.name != "continue_conversation":
        graph.add_edge(tool_obj.name, "Decison_Maker")

# if anything goes to final answer, it must then move to END
graph.add_edge("generate_final_output", END)

chatClient = graph.compile()

def main():

    print("-"*100)
    print("아치: 안녕하세요! Hello!")

    while True:
        userInput = input("User: ")

        if userInput.lower() == "exit":
            break
        
        # Add the user's message to the conversation memory
        chatHistory.append(HumanMessage(content=userInput))

        # Define context separately if needed (e.g., additional information)
        contextCombined =  ""

        # Invoke the agent with the user input and the current chat history
        response = decisionMaker.invoke({"chat_history": chatHistory, "input": userInput, "intermediate_steps": [],})
        print(response)

        # Add the agent's response to the conversation memory
        chatHistory.append(AIMessage(content=response["output"]))

        #debugging code
        #print(chatHistory.buffer_as_messages)
        
    # # add chat history to firebase firestore cloud 
    # firestoreClient.add_messages(chatHistory)

main()


