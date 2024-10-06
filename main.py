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
from langchain_core.prompts import MessagesPlaceholder, ChatPromptTemplate
from langchain_core.tools import Tool
from langchain.memory import ConversationBufferMemory
from langchain.agents import AgentExecutor, create_structured_chat_agent, create_react_agent, create_tool_calling_agent
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore



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
VECTORSTORE_INDEX_NAME = "archie"
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

print("initializing chat history")
cloudChatHistory = FirestoreChatMessageHistory(
    session_id = SESSION_ID,
    collection = COLLECTION_NAME,
    client = firestoreClient,
)
print("initializing Finished")

#init pincone/vector store 
print("Initializing vectorstore")
db = PineconeVectorStore(index= VECTORSTORE_INDEX_NAME, embedding=embeddings)
print("Initializing Finished")

responseList = []
####--------initialization ends--------####


def getRelevantOutput(newInput, chatHistory):
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
    # `create_stuff_documents_chain` feeds all retrieved context into the LLM
    question_answer_chain = create_stuff_documents_chain(openAIClient, qa_prompt)

    # Create a retrieval chain that combines the history-aware retriever and the question answering chain
    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    response = rag_chain.invoke({"input": newInput, "chat_history": chatHistory})

    #append to the answer List
    responseList.append({response['answer']})


def listToString(docList):
    ret = ""

    for doc in docList:
        ret = ret + doc
    
    return ret


# Retrieve the chat history from FirestoreChatMessageHistory and convert to list of base messages
def getChatHistory(cloudChatHistory):
    # Initialize a ConversationBufferMemory
    memory = ConversationBufferMemory()

    # Iterate over messages from cloud history and add to memory
    for entry in cloudChatHistory.messages:
        if entry["role"] == "user":
            memory.chat_memory.add_message(HumanMessage(content=entry["content"]))
        elif entry["role"] == "assistant":
            memory.chat_memory.add_message(AIMessage(content=entry["content"]))

    # Return the memory instance with all messages added
    return memory

def getResponseList():
    return responseList

####--------tools for the agent--------####
tools = [
    Tool(
        name="generate relevant insight",
        func=getRelevantOutput,
        description="appends the specific advice for the latest question to a list",
    ),
    Tool(
        name="returns the list as a string",
        func=listToString,
        description="returns a string output when list is given"
    ),
    Tool(
        name="get insight list",
        func=getResponseList,
        description="access the insight that were created by 'generate relevant insight', this should be used for the final insight output"
    )
]
####--------tools end--------####


####--------creating agent--------####
#creating prompt template
agentSystemPrompt = ChatPromptTemplate.from_messages(
    [
        ("system", prompt.agentSystemPrompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ]
)

# create_structured_chat_agent initializes a chat agent designed to interact using a structured prompt and tools
# It combines the language model (llm), tools, and prompt to create an interactive agent
agent = create_structured_chat_agent(
    llm = openAIClient,
    tools = tools,
    prompt = agentSystemPrompt,
)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, handle_parsing_errors=False, verbose=True,
)
####--------creating agent ends--------####


def main():
    
    chatHistory = getChatHistory(cloudChatHistory)

    while True:
        user_input = input("User: ")
        if user_input.lower() == "exit":
            break

        # Add the user's message to the conversation memory
        chatHistory.chat_memory.add_message(HumanMessage(content=user_input))

        # Invoke the agent with the user input and the current chat history
        response = agent_executor.invoke({"input": user_input, "chat_history": chatHistory.buffer_as_messages})
        print("Bot:", response["output"])

        # Add the agent's response to the conversation memory
        chatHistory.chat_memory.add_message(AIMessage(content=response["output"]))
        
    # add chat history to firebase firestore cloud 
    firestoreClient.add_messages(chatHistory)

main()


