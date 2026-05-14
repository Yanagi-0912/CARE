from langgraph.graph import MessagesState


class State(MessagesState):
    allow_rag: bool
    call_request_location: bool
