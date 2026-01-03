from fastapi import APIRouter, Depends

from app.core.security import get_current_user, CurrentUser


router = APIRouter(prefix="/auth", tags=["auth"])

#  Get current authenticated user info.
@router.get("/me")
def get_me(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
   
    return current_user
