import os

from dotenv import load_dotenv
from langchain import hub
from langchain.agents import AgentExecutor, create_react_agent
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_community.vectorstores import Chroma
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from google.cloud import firestore
from langchain_google_firestore import FirestoreChatMessageHistory
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

print("initializing chat history")
cloudChatHistory = FirestoreChatMessageHistory(
    session_id = SESSION_ID,
    collection = COLLECTION_NAME,
    client = firestoreClient,
)
print("initializing Finished")

#init pincone/vector store 
print("Initializing vectorstore")
db = PineconeVectorStore(index= VECTORSTORE_INDEX, embedding=embeddings)
print("Initializing Finished")

insightList = []
####--------initialization ends--------####

# Create a retriever for querying the vector store
# `search_type` specifies the type of search (e.g., similarity)
# `search_kwargs` contains additional arguments for the search (e.g., number of results to return)
retriever = db.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 3},
)


# Contextualize question prompt
# This system prompt helps the AI understand that it should reformulate the question
# based on the chat history to make it a standalone question
contextualize_q_system_prompt = (
    "Given a chat history and the latest user question "
    "which might reference context in the chat history, "
    "formulate a standalone question which can be understood "
    "without the chat history. Do NOT answer the question, just "
    "reformulate it if needed and otherwise return it as is."
)

# Create a prompt template for contextualizing questions
contextualize_q_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", contextualize_q_system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ]
)

# Create a history-aware retriever
# This uses the LLM to help reformulate the question based on chat history
history_aware_retriever = create_history_aware_retriever(
    openAIClient, retriever, contextualize_q_prompt
)

# Answer question prompt
# This system prompt helps the AI understand that it should provide concise answers
# based on the retrieved context and indicates what to do if the answer is unknown
qa_system_prompt = (
    "You are an assistant for question-answering tasks. Use "
    "the following pieces of retrieved context to answer the "
    "question. If you don't know the answer, just say that you "
    "don't know. Use three sentences maximum and keep the answer "
    "concise."
    "\n\n"
    "{context}"
)

# Create a prompt template for answering questions
qa_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", qa_system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ]
)

# Create a chain to combine documents for question answering
# `create_stuff_documents_chain` feeds all retrieved context into the LLM
question_answer_chain = create_stuff_documents_chain(openAIClient, qa_prompt)

# Create a retrieval chain that combines the history-aware retriever and the question answering chain
rag_chain = create_retrieval_chain(
    history_aware_retriever, question_answer_chain)


# Set Up ReAct Agent with Document Store Retriever
# Load the ReAct Docstore Prompt
react_docstore_prompt = hub.pull("wkdghdus/archie2")

def appendInsightToLocalMemory(insight):
    insightList.append(insight)

def getLocalMemoryToString():
    ret = ""
    for insight in insightList:
        ret += insight
    return ret

tools = [
    Tool(
        name="insight gatherer",
        func=lambda input, **kwargs: rag_chain.invoke(
            {"user_input": input, "chat_history": kwargs.get("chat_history", [])}
        ),
        description="gathers insight from vector database",
    ),
    Tool(
        name="append insight",
        func=appendInsightToLocalMemory,
        description="append its parameter to local memory for later use",
    ),
    Tool(
        name="get insight",
        func=getLocalMemoryToString,
        description="Access/returns local memory in string form",   
    )
]

# Create the ReAct Agent with document store retriever
agent = create_react_agent(
    llm=openAIClient,
    tools=tools,
    prompt=react_docstore_prompt,
)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, handle_parsing_errors=True, verbose=True,
)

chat_history = []
while True:
    query = input("You: ")
    if query.lower() == "exit":
        break
    response = agent_executor.invoke(
        {"input": query, "chat_history": chat_history})
    print(f"AI: {response['output']}")

    # Update history
    chat_history.append(HumanMessage(content=query))
    chat_history.append(AIMessage(content=response["output"]))