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
from pydantic import BaseModel, Field
from langchain.tools import BaseTool, StructuredTool, tool, Tool
from langchain.memory import ConversationBufferMemory
from langchain.agents import AgentExecutor, create_structured_chat_agent, create_react_agent, create_tool_calling_agent
from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore
from langchain import hub


#load env variables
load_dotenv()

####--------CONSTANTS/상수--------####
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

####--------CONSTANTS/상수 ends--------####


####--------initialization--------####
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

insightList = []    # list for insight
chatHistory = []    # list for chat history

###########for cloud chat history storing ##########
# print("initializing chat history")
# cloudChatHistory = FirestoreChatMessageHistory(
#     session_id = SESSION_ID,
#     collection = COLLECTION_NAME,
#     client = firestoreClient,
# )
# print("initializing Finished")
###########for cloud chat history storing ##########

####--------initialization ends--------####


####--------Functions--------####

# # Retrieve the chat history from FirestoreChatMessageHistory and convert to list of base messages
# def getCloudChatHistory(cloudChatHistory):
#     # Initialize a ConversationBufferMemory
#     memory = ConversationBufferMemory(return_messages=True)

#     # Iterate over messages from cloud history and add to memory
#     for entry in cloudChatHistory.messages:
#         if entry["role"] == "user":
#             memory.chat_memory.add_message(HumanMessage(content=entry["content"]))
#         elif entry["role"] == "assistant":
#             memory.chat_memory.add_message(AIMessage(content=entry["content"]))

#     # Return the memory instance with all messages added
#     return memory


def listToString(docList):
    ret = ""
    for doc in docList:
        ret = ret + doc
    
    return ret


def getInsightList():
    return insightList


def getRelevantOutput(newInput, chatHistory=chatHistory):

    ####--------Creating vector store retrievor--------####

    #vector store retriever
    #connected to scientific data vector db
    retriever = db.as_retriever()

    # Contextualize question prompt
    # This system prompt helps the AI understand that it should reformulate the question
    # based on the chat history to make it a standalone question
    contextualizeQSystemPrompt = prompt.contextual_q_system_prompt

    # Create a prompt template for contextualizing questions
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualizeQSystemPrompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )

    # Create a history-aware retriever
    # This uses the LLM to help reformulate the question based on chat history
    history_aware_retriever = create_history_aware_retriever(
        openAIClient, retriever, contextualize_q_prompt
    )
    ####--------Retriever creation ends--------####

    ####--------Creating question answering chain--------####
    # Answer question prompt
    # This system prompt helps the AI understand that it should provide concise answers
    # based on the retrieved context and indicates what to do if the answer is unknown
    qaSystemPrompt = prompt.qa_system_prompt

    # Create a prompt template for answering questions
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", qaSystemPrompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )

    # Create a chain to combine documents for question answering
    # create_stuff_documents_chain feeds all retrieved context into the LLM
    question_answer_chain = create_stuff_documents_chain(openAIClient, qa_prompt)

    # Create a retrieval chain that combines the history-aware retriever and the question answering chain
    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    response = rag_chain.invoke({"input": newInput, "chat_history": chatHistory})

    #append to the answer List
    insightList.append({response['answer']})

    return "insight has been generated, you are allowed to proceed to next process"

##############used for structuredTool.from_function#################
# class GetRelevantOutputArgs(BaseModel):
#     newInput: str = Field(description="most recent user input")
#     chatHistory: list = Field(description="chat history")


####--------tools for the agent--------####
tools = [
    # StructuredTool.from_function(
    #     name="append relevant insight",
    #     func=getRelevantOutput,
    #     description="appends the specific advice for the latest question to a list that will be used in the final insight output. Returns True if done successfully. If you get True from this tool, move on to the next thought",
    #     args_schema=GetRelevantOutputArgs,
    # ),
    Tool(
        name="append relevant insight",
        func=getRelevantOutput,
        description="appends the specific advice for the latest question to a list that will be used in the final insight output. When given latest user input and chat history Returns String if done successfully. If you get string from this tool, move on to the next thought",
    ),
    Tool(
        name="returns the list as a string",
        func=listToString,
        description="returns a string output when list is given"
    ),
    Tool(
        name="get insight list",
        func=getInsightList,
        description="access the insight that were created by 'generate relevant insight', this should be used for the final insight output"
    ),
]
####--------tools end--------####


####--------creating agent--------####

# creating prompt template
agentSystemPrompt = ChatPromptTemplate.from_messages(
    [
        ("system", prompt.reactAgentPrompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ]
)

# test code
# agentSystemPrompt = hub.pull("wkdghdus/archie")

# create_structured_chat_agent initializes a chat agent designed to interact using a structured prompt and tools
# It combines the language model (llm), tools, and prompt to create an interactive agent
agent = create_react_agent(
    llm = openAIClient,
    tools = tools,
    prompt = agentSystemPrompt,
)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, handle_parsing_errors=True, verbose=True, 
)
####--------creating agent ends--------####


def main():

    print("-"*100)
    print("아치: 안녕하세요! Hello!")

    while True:
        user_input = input("User: ")

        if user_input.lower() == "exit":
            break
        
        # Add the user's message to the conversation memory
        chatHistory.append(HumanMessage(content=user_input))

        # Define context separately if needed (e.g., additional information)
        contextCombined = "This is additional context"  

        # Invoke the agent with the user input and the current chat history
        response = agent_executor.invoke({"chat_history": chatHistory, "context": contextCombined, "input": user_input})
        print("아치:", response["output"])

        # Add the agent's response to the conversation memory
        chatHistory.append(AIMessage(content=response["output"]))

        #debugging code
        #print(chatHistory.buffer_as_messages)
        
    # # add chat history to firebase firestore cloud 
    # firestoreClient.add_messages(chatHistory)

main()


