from fastapi import APIRouter

from jobtology_be.api.analyses import router as analyses_router
from jobtology_be.api.capabilities import router as capabilities_router
from jobtology_be.api.goals import router as goals_router
from jobtology_be.api.m5_queries import router as m5_queries_router
from jobtology_be.api.preferences import router as preferences_router
from jobtology_be.api.profiles import router as profiles_router
from jobtology_be.api.roadmap_diffs import router as roadmap_diffs_router
from jobtology_be.api.roadmaps import router as roadmaps_router

router = APIRouter(tags=["product"])
router.include_router(analyses_router)
router.include_router(capabilities_router)
router.include_router(goals_router)
router.include_router(m5_queries_router)
router.include_router(preferences_router)
router.include_router(profiles_router)
router.include_router(roadmap_diffs_router)
router.include_router(roadmaps_router)
