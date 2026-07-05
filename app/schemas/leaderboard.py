from pydantic import BaseModel
from typing import List, Optional

class LeaderboardUser(BaseModel):
    id: int
    username: str
    profile_image_url: Optional[str] = None
    rank: int
    score: float

class LeaderboardResponse(BaseModel):
    top_users: List[LeaderboardUser]
    current_user: Optional[LeaderboardUser] = None
