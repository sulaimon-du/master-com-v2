"""All SQLAlchemy models are imported here so Alembic sees them."""

from app.models.auth import AuthSession, AuthToken, GoogleClaim, OAuthFlow, RateLimit
from app.models.catalog import Category, Specialty, SpecialtySynonym
from app.models.complaint import Complaint
from app.models.imports import DataRemovalRequest, ImportBlocklist, ProfileClaim
from app.models.master import MasterArea, MasterProfile, MasterSpecialty
from app.models.place import City, District
from app.models.portfolio import PortfolioCollection, PortfolioPhoto
from app.models.request import JobRequest, JobResponse, RequestPhoto, RequestSpecialty
from app.models.review import Review
from app.models.support import SupportMessage, SupportThread
from app.models.user import User

__all__ = [
    "AuthSession",
    "AuthToken",
    "Category",
    "City",
    "Complaint",
    "DataRemovalRequest",
    "District",
    "GoogleClaim",
    "ImportBlocklist",
    "JobRequest",
    "JobResponse",
    "MasterArea",
    "MasterProfile",
    "MasterSpecialty",
    "OAuthFlow",
    "PortfolioCollection",
    "PortfolioPhoto",
    "ProfileClaim",
    "RateLimit",
    "RequestPhoto",
    "RequestSpecialty",
    "Review",
    "Specialty",
    "SpecialtySynonym",
    "SupportMessage",
    "SupportThread",
    "User",
]
