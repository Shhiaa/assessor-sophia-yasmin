from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(..., description="The message to send to the chatbot")

class ChatResponse(BaseModel):
    response: str = Field(..., description="The chatbot's response to the user's message")

class ChatHistory(BaseModel):
    role:    str = Field(..., description="Quem enviou a mensagem: 'human' ou 'assistant'")
    content: str = Field(..., description="O conteúdo da mensagem")

class SessionResponse(BaseModel):
    session_id: str
    resumo:     str | None = None
