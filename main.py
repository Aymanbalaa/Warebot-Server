import asyncio
import json
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Dict, Any

# --- IMPORTANT: UPDATE THIS WITH YOUR RASPBERRY PI's IP ADDRESS ---
ROBOT_BRIDGE_URL = "ws://100.98.40.61:9090"

# --- Connection Manager for Browser Clients ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# --- Global State & Robot Connection ---
robot_state: Dict[str, Any] = {"pose": {"x": 0.0, "y": 0.0}}
websocket_connection = None

# --- Pydantic Model for incoming goal data ---
class Goal(BaseModel):
    x: float
    y: float

# --- Background Task to manage robot connection ---
async def robot_connection_manager():
    global websocket_connection
    while True:
        try:
            async with websockets.connect(ROBOT_BRIDGE_URL) as websocket:
                websocket_connection = websocket
                print(f"Successfully connected to robot at {ROBOT_BRIDGE_URL}")
                
                # Subscribe to a topic (e.g., your robot's pose) upon connection
                await websocket.send(json.dumps({
                    "op": "subscribe",
                    "topic": "/amcl_pose", # Change to your actual robot pose topic
                    "type": "geometry_msgs/msg/PoseWithCovarianceStamped"
                }))

                async for message in websocket:
                    data = json.loads(message)
                    
                    # Store latest state and broadcast to all browser clients
                    if data.get("topic") == "/amcl_pose":
                        pose = data['msg']['pose']['pose']['position']
                        robot_state["pose"]["x"] = pose['x']
                        robot_state["pose"]["y"] = pose['y']
                    
                    await manager.broadcast(json.dumps(robot_state))

        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"Connection to robot failed: {e}. Retrying in 5 seconds...")
            websocket_connection = None
            await asyncio.sleep(5)

# --- FastAPI Lifespan & Application ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Server starting up...")
    asyncio.create_task(robot_connection_manager())
    yield
    print("Server shutting down...")

app = FastAPI(lifespan=lifespan)

# --- API Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def get_homepage():
    try:
        with open("index.html") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html not found</h1>", status_code=404)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection open
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/robot/goals")
async def send_navigation_goal(goal: Goal):
    if websocket_connection is None:
        return {"status": "error", "message": "Robot not connected"}

    goal_message = {
        "op": "publish",
        "topic": "/goal_pose",
        "msg": {
            "header": {"frame_id": "map"},
            "pose": {
                "position": {"x": goal.x, "y": goal.y, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
            }
        }
    }
    await websocket_connection.send(json.dumps(goal_message))
    return {"status": "success", "message": f"Sent goal {goal.x}, {goal.y} to robot."}