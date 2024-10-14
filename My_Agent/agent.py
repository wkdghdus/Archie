import os
import My_Agent.utils.prompt as prompt
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
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
from langchain.tools import BaseTool, StructuredTool, tool, Tool
from langchain.memory import ConversationBufferMemory
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain import hub
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent
from typing import Annotated, List, TypedDict, Union
import My_Agent.utils.prompt as prompt
import operator


#load env variables
load_dotenv()

####-------- CONSTANTS/상수 --------####
#For OpenAI ChatGPT
GPT_MODEL = "gpt-4o-mini"
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

# print("initializing firestore")
# #Init Firebase history storage
# firestoreClient = firestore.Client(project = PROJECT_ID)
# print("initializing Finished")

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
    for doc in response:
        sourceList.append(doc)

    return "Relevant sources are successfully saved in the memory" #파란 텍스트 부분
    ####--------End function--------####

@tool("generate_final_output")
def generateFinalOutput(
    finalChatHistory: Annotated[List, "chat history"] = chatHistory):
    """generates final output for the user. Used when all the question is asked and answered."""

    parser = StrOutputParser()

    finalOutputPrompt = ChatPromptTemplate.from_messages(
        [
            ("system", prompt.finalOutputPrompt),
        ]
    )

    finalChain = create_stuff_documents_chain(llm=openAIClient, prompt=finalOutputPrompt)

    response = finalChain.invoke({"chat_history": finalChatHistory, "context": sourceList})

    print(response)



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

    fullResponse = reaction + " " + nextQuestion

    print("아치: " + fullResponse)

    chatHistory.append(AIMessage(content= fullResponse))

    return fullResponse

#list of the tools used by LLM, will be passed as variables and parameters.
tools = [getRelevantSources, generateFinalOutput, continueConversation]

####-------- DEFINING TOOLS FOR AGENT ENDS --------####



####-------- HELPER FUNCTIONS --------####

def listToString(docList):
    ret = ""
    for doc in docList:
        ret = ret + doc.toString()
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
#this outputs the decision to use certain tool
def runDecisionMaker(state: list):

    ##debug code
    # print("run decision maker")
    # print(f"intermediate_steps: {state['intermediate_steps']}")
    ##debug code end

    #invoke decision maker
    out = decisionMaker.invoke(state)

    #assign tool and tool input variable as the first tool that decision maker invokes
    tool_name = out.tool_calls[0]["name"]
    tool_args = out.tool_calls[0]["args"]

    #create action agent
    action_out = AgentAction(
        tool=tool_name,
        tool_input=tool_args,
        log="TBD"
    )

    #return intermediate step that it should take next
    return {
        "intermediate_steps": [action_out]
    }

#decision router that reads intermediate step and direct to the most recent call. 
def router(state: list):
    # return the tool to use
    if isinstance(state["intermediate_steps"], list):
        #look at the most recent intermidiate step made by runDecisionMaker 
        # returns the name of it
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

# 
def run_tool(state: list):
    # parse the tool that should be used. Decision is made in runDecisionMaker prior
    tool_name = state["intermediate_steps"][-1].tool
    tool_args = state["intermediate_steps"][-1].tool_input

    ##debug tool
    #print(f"{tool_name}.invoke(input={tool_args})")     #print statement to just keep track
    ##debug tool ends

    # run tool
    out = tool_str_to_func[tool_name].invoke(input=tool_args)

    #from agent action to append to intermediate step
    action_out = AgentAction(
        tool=tool_name,
        tool_input=tool_args,
        log=str(out)
    )

    return {"intermediate_steps": [action_out]}

####--------Define Nodes for Graph End--------####



####-------- Define Graph --------####
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

graph.add_edge("gather_relevant_sources", "Decision_Maker")

# create edges from each tool back to the oracle
#use this code when you add more nodes later in production.
# for tool_obj in tools:
#     if (tool_obj.name != "generate_final_output") and (tool_obj.name != "continue_conversation"):
#         graph.add_edge(tool_obj.name, "Decision_Maker")

# if anything goes to final answer or continue conversation, move to END
graph.add_edge("continue_conversation", END)
graph.add_edge("generate_final_output", END)

chatClient = graph.compile()
####-------- Defining Graph Ends --------####


