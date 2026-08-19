"""Ports the application uses to reach the SAT (implemented by infrastructure.sat)."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, SubmitResult, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId, Rfc


class AuthenticationGateway(Protocol):
    def authenticate(self, identity: SigningIdentity) -> AccessToken: ...


class RequestGateway(Protocol):
    def request(self, query: DownloadQuery, token: AccessToken) -> SubmitResult: ...


class VerificationGateway(Protocol):
    def verify(self, request_id: RequestId, rfc: Rfc, token: AccessToken) -> VerificationResult: ...


class DownloadGateway(Protocol):
    def download(self, package_id: PackageId, rfc: Rfc, token: AccessToken) -> Package: ...
