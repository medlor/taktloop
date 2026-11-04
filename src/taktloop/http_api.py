"""Plain HTTP API: `POST /api/<tool>` with a JSON body, over the same operations as the MCP tools."""

from pydantic import ConfigDict, ValidationError, validate_call
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from taktloop.auth import roles_from_scopes
from taktloop.service import Caller, Rejected


def routes(ops: dict, factory: str, verifier) -> list[Route]:
    async def call(request: Request) -> JSONResponse:
        tool = request.path_params["tool"]
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
        token = await verifier.verify_token(bearer) if scheme.lower() == "bearer" and bearer else None
        if token is None or not token.subject:
            return JSONResponse({"reason": "not authenticated"}, 401)
        if tool not in ops:
            return JSONResponse({"reason": f"unknown operation {tool}"}, 404)
        caller = Caller(token.subject, roles_from_scopes(token.scopes, factory), executor="http")
        try:
            args = await request.json() if await request.body() else {}
            checked = validate_call(ops[tool], config=ConfigDict(arbitrary_types_allowed=True))
            result = checked(caller, **args)
        except (ValidationError, ValueError, TypeError) as e:  # bad JSON, non-object body, bad arguments
            return JSONResponse({"reason": f"invalid arguments: {str(e).splitlines()[0]}"}, 422)
        except Rejected as e:
            return JSONResponse({"reason": e.reason}, 409)
        return JSONResponse(result.model_dump(mode="json") if result else None)

    return [Route("/api/{tool}", call, methods=["POST"])]
