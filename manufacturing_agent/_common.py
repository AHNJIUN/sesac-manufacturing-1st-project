from __future__ import annotations

import os
import re
import json
import sqlite3
#허수정
import enum
#허수정
import datetime as _dt
from contextlib import contextmanager, closing
from typing import Any, Optional, Literal, TypeAlias, Annotated
#허수정
from typing_extensions import TypedDict
from dataclasses import dataclass, field
from collections import Counter
#허수정

# --- LangGraph (필수) ---
from langgraph.graph import StateGraph, START, END
#허수정
from langgraph.graph import MessagesState, add_messages # 추가 -> 이유: 채팅 모델 사용 시 메시지 누적 필요
from langgraph.checkpoint.memory import MemorySaver
#허수정
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

#허수정
# --- ToolNode (prediction explorer 서브그래프에서 bound tools 실행) ---
try:
    from langgraph.prebuilt import ToolNode
    _HAS_TOOLNODE = True
except Exception:
    ToolNode = None
    _HAS_TOOLNODE = False

# --- LLM 메시지/툴 데코레이터 --- 
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage, BaseMessage,
)
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig   # config/runnableconfig로 값 추출

# --- 단기/장기 체크포인터 ---
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    _HAS_SQLITE_SAVER = True
except Exception:
    SqliteSaver = None
    _HAS_SQLITE_SAVER = False

# --- pydantic은 langchain_core 의존성으로 보통 함께 설치됨 ---
from pydantic import BaseModel, Field, ValidationError
from annotated_types import MinLen
#허수정

#허수정
#허수정

# import * 가 밑줄(_x) 이름까지 가져오도록 명시 export
__all__ = [n for n in dir() if not n.startswith("__")]
