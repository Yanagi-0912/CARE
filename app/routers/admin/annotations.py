from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.dependencies import CurrentUser, get_user_profile_service, require_admin_user
from app.models.annotation import SaveAnnotationRequest
from app.services.annotations import AnnotationService
from app.services.users.user_profile_service import UserProfileService

router = APIRouter(dependencies=[Depends(require_admin_user)])


def get_annotation_service():
    return AnnotationService()


@router.get("")
async def list_annotation_tasks(
    response: Response,
    service: AnnotationService = Depends(get_annotation_service),
):
    response.headers["Cache-Control"] = "no-store"
    return service.catalog()


@router.get("/{task}/session")
async def get_annotation_session(
    task: str,
    response: Response,
    user: CurrentUser = Depends(require_admin_user),
    profiles: UserProfileService = Depends(get_user_profile_service),
    service: AnnotationService = Depends(get_annotation_service),
):
    response.headers["Cache-Control"] = "no-store"
    profile = await profiles.get_user_profile(user.line_user_id)
    return await service.session(task, user.line_user_id, (profile or {}).get("name") or "")


@router.put("/{task}/answers/{position}")
async def save_annotation(
    task: str,
    position: Annotated[int, Path(ge=0)],
    body: SaveAnnotationRequest,
    response: Response,
    user: CurrentUser = Depends(require_admin_user),
    service: AnnotationService = Depends(get_annotation_service),
):
    response.headers["Cache-Control"] = "no-store"
    return await service.save(task, user.line_user_id, position, body)


@router.get("/{task}/export")
async def export_annotations(
    task: str,
    response: Response,
    user: CurrentUser = Depends(require_admin_user),
    profiles: UserProfileService = Depends(get_user_profile_service),
    service: AnnotationService = Depends(get_annotation_service),
):
    response.headers["Cache-Control"] = "no-store"
    profile = await profiles.get_user_profile(user.line_user_id)
    return await service.export(task, user.line_user_id, (profile or {}).get("name") or "")
