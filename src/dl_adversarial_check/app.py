"""FastAPI gateway for adversarial deception hunting.

This gateway:
1. Accepts chat completions requests
2. Injects B's adversarial system prompt
3. Executes search tools to gather pro/con evidence
4. Re-runs B with evidence summaries
5. Returns deception_level based on evidence balance
"""
from __future__ import annotations

import json
from typing import Optional, Any
import os

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import httpx
import structlog

from dl_tools_lib import WebFetcher, SearchEngine
from .prompts import B_SYSTEM_ADVERSARIAL
from .tools import TOOLS_SCHEMA

log = structlog.get_logger(__name__)

app = FastAPI(title="dl-adversarial-check")


class ChatCompletionRequest(BaseModel):
    model: str = "lobe-a"
    messages: list[dict[str, Any]]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[list[dict]] = None


class AdversarialGateway:
    """Gateway that injects adversarial prompts and hunts for deception."""
    
    def __init__(
        self,
        provider_base_url: str,
        provider_api_key: str,
        provider_model: str,
    ):
        self.provider_base_url = provider_base_url.rstrip("/")
        self.provider_api_key = provider_api_key
        self.provider_model = provider_model
        self.client = httpx.AsyncClient(
            base_url=self.provider_base_url,
            headers={"Authorization": f"Bearer {provider_api_key}"},
            timeout=60.0,
        )
        self.log = structlog.get_logger(__name__)
    
    async def close(self):
        await self.client.aclose()
    
    async def call_provider(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Call the LLM provider with messages and tools."""
        payload = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        
        response = await self.client.post("/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()
    
    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, str],
    ) -> str:
        """Execute an adversarial tool and return result."""
        try:
            if tool_name == "search":
                query = arguments.get("query", "")
                source = arguments.get("source", "duckduckgo")
                if not query:
                    return json.dumps({"error": "Query required"})
                
                async with SearchEngine() as search_engine:
                    if source == "wikipedia":
                        results = await search_engine.search_wikipedia(query, max_results=3)
                    else:
                        results = await search_engine.search_duckduckgo(query, max_results=3)
                    
                    return json.dumps({
                        "query": query,
                        "source": source,
                        "results": [
                            {
                                "title": r.title,
                                "url": r.url,
                                "snippet": r.snippet,
                            }
                            for r in results.results
                        ],
                    })
            
            elif tool_name == "fetch_web":
                url = arguments.get("url", "")
                if not url:
                    return json.dumps({"error": "URL required"})
                
                async with WebFetcher() as fetcher:
                    result = await fetcher.fetch(url)
                    if result.status_code == 200:
                        return json.dumps({
                            "url": url,
                            "status": 200,
                            "content": result.content[:2000],  # Longer content for detailed evidence
                        })
                    else:
                        return json.dumps({
                            "url": url,
                            "status": result.status_code,
                            "error": result.error,
                        })
            
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        
        except Exception as e:
            self.log.exception("tool_execution_error", tool=tool_name, exc=e)
            return json.dumps({"error": str(e)[:200]})
    
    async def handle_adversarial_request(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Handle a request with B's adversarial hunting flow.
        
        1. Call provider with B's adversarial prompt + tools
        2. If provider requests searches, execute them
        3. Re-run provider with evidence results
        4. Return observations with deception_level
        """
        # Inject B's adversarial system prompt
        messages_with_system = [
            {"role": "system", "content": B_SYSTEM_ADVERSARIAL},
            *messages,
        ]
        
        # Call provider with adversarial tools
        response = await self.call_provider(
            messages_with_system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=TOOLS_SCHEMA,
        )
        
        # Check if provider requested tool use
        choice = response.get("choices", [{}])[0]
        if choice.get("finish_reason") == "tool_calls":
            # Execute tool requests
            tool_calls = choice.get("tool_calls", [])
            tool_results = []
            
            for tool_call in tool_calls:
                tool_name = tool_call["function"]["name"]
                arguments = json.loads(tool_call["function"]["arguments"])
                
                self.log.info("executing_adversarial_search", tool=tool_name, query=arguments.get("query"))
                result = await self.execute_tool(tool_name, arguments)
                
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": result,
                })
            
            # Re-run provider with evidence results
            follow_up_messages = [
                *messages_with_system,
                {"role": "assistant", "content": choice.get("content", ""), "tool_calls": tool_calls},
                *tool_results,
            ]
            
            response = await self.call_provider(
                follow_up_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        
        return response


# Initialize gateway
gateway = None


@app.on_event("startup")
async def startup():
    global gateway
    provider_base_url = os.getenv("PROVIDER_BASE_URL", "https://api.openai.com/v1")
    provider_api_key = os.getenv("PROVIDER_API_KEY", "")
    provider_model = os.getenv("PROVIDER_MODEL", "gpt-4o")
    
    gateway = AdversarialGateway(
        provider_base_url=provider_base_url,
        provider_api_key=provider_api_key,
        provider_model=provider_model,
    )


@app.on_event("shutdown")
async def shutdown():
    global gateway
    if gateway:
        await gateway.close()


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Handle chat completion requests with adversarial checking."""
    global gateway
    
    if not gateway:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    
    try:
        return await gateway.handle_adversarial_request(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except Exception as e:
        log.exception("request_error", exc=e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/healthz")
async def health():
    return {"status": "ok"}
