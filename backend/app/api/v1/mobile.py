"""Mobile-only endpoints (push token registration)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import get_current_user
from app.core.database import get_session
from app.models.expo_push_token import ExpoPushToken
from app.models.user import User

router = APIRouter(prefix="/mobile", tags=["mobile"])


class PushTokenIn(BaseModel):
    token: str
    device_id: str
    platform: str

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, v: str) -> str:
        vv = v.lower().strip()
        if vv not in {"ios", "android"}:
            raise ValueError("platform must be 'ios' or 'android'")
        return vv


@router.post("/push-token", status_code=status.HTTP_200_OK)
async def register_push_token(
    body: PushTokenIn,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    token = body.token.strip()
    device_id = body.device_id.strip()
    if not token.startswith("ExponentPushToken["):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid Expo push token format")

    # Deactivate any existing active row for this device.
    existing_device = await session.execute(
        select(ExpoPushToken).where(
            ExpoPushToken.device_id == device_id,
            ExpoPushToken.is_active == True,  # noqa: E712
        )
    )
    for row in existing_device.scalars().all():
        row.is_active = False
        row.tbl_last_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(row)

    # Upsert active token row for this user/device.
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    new_row = ExpoPushToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token=token,
        device_id=device_id,
        platform=body.platform,
        is_active=True,
        registered_at=now_ts,
        tbl_last_dt=now_ts,
    )
    session.add(new_row)
    await session.commit()

    return {"success": True, "data": {"registered": True}}


@router.delete("/push-token", status_code=status.HTTP_200_OK)
async def deregister_push_tokens(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(ExpoPushToken).where(
            ExpoPushToken.user_id == user.id,
            ExpoPushToken.is_active == True,  # noqa: E712
        )
    )
    items = rows.scalars().all()
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in items:
        row.is_active = False
        row.tbl_last_dt = now_ts
        session.add(row)

    await session.commit()
    return {"success": True, "data": {"deregistered": len(items)}}
