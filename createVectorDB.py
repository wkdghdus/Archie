import os
# from openai import OpenAI
# from dotenv import load_dotenv, find_dotenv
# from pinecone import Pinecone
from langchain_community.document_loaders import PyPDFLoader
from langchain_pinecone import PineconeVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import CharacterTextSplitter


#API KEY ASSIGNMENT
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")       #openAI API key
#PINECONE_API_KEY = os.getenv('PINECONE_API_KEY')        #PineCone API key

indexName = "archie"

#embedding model using langchain OpenAIEmbeddings, model is text-embedding-3-small
embeddings = OpenAIEmbeddings(openai_api_key= OPENAI_API_KEY, model = "text-embedding-3-small")

#text splitter to split PDF contents into smaller chunks
textSplitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)


def loadDocAndAddEmbed(source, vectorStoreFromDoc):
    print("currently embedding: " + source)
    try: 
        loader = PyPDFLoader(source)
        data = loader.load()
        docs = textSplitter.split_documents(data)
        vectorStoreFromDoc.add_documents(docs)
    except:
        print(Exception.with_traceback)
        print("Failed to add embedding: "+ source)



def createVectorDB():
    vectorStore = PineconeVectorStore(index_name = indexName, embedding = embeddings)

    #iterate through all the PDF files in Data directory. 
    directory = os.fsencode("Data")
        
    for file in os.listdir(directory):
        fileName = os.fsdecode(file)
        loadDocAndAddEmbed("Data/" + fileName, vectorStore)


createVectorDB()