"""
Authentication API Router.
"""

from typing import Optional
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from app.api.v1.auth.dependencies import (
    get_current_active_user,
    get_google_login_use_case,
    get_login_use_case,
    get_logout_use_case,
    get_refresh_use_case,
    get_register_use_case,
)
from app.core.config import settings
from app.modules.auth.application.use_cases.google_login import (
    GoogleAuthFailedError,
    GoogleLoginUseCase,
)
from app.modules.auth.application.use_cases.login import (
    InactiveUserError,
    InvalidCredentialsError,
    LoginUseCase,
)
from app.modules.auth.application.use_cases.logout import LogoutUseCase
from app.modules.auth.application.use_cases.refresh import RefreshUseCase
from app.modules.auth.application.use_cases.register import (
    RegisterUseCase,
    UserAlreadyExistsError,
)
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.oauth.google import (
    GoogleOAuthError,
    GoogleOAuthNotConfiguredError,
)
from app.modules.auth.infrastructure.repositories.refresh_token_repository import (
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    RefreshTokenReusedError,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    id_token: str


class GoogleExchangeRequest(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


class GoogleAuthUrlResponse(BaseModel):
    authorization_url: str
    state: str


class UserResponse(BaseModel):
    id: Optional[int]
    email: str
    full_name: Optional[str] = None
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    refresh_token: Optional[str] = None


class MessageResponse(BaseModel):
    message: str
    success: bool = True


def _set_refresh_cookie_if_enabled(response: Response, refresh_token: Optional[str]) -> None:
    """Sets HttpOnly Secure SameSite cookie if AUTH_REFRESH_MODE is cookie."""
    if settings.AUTH_REFRESH_MODE.lower() == "cookie" and refresh_token and response is not None:
        response.set_cookie(
            key="sentinews_refresh_token",
            value=refresh_token,
            httponly=True,
            secure=settings.ENVIRONMENT.lower() == "production",
            samesite="lax",
            max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            path=f"{settings.API_V1_STR}/auth",
        )


def _clear_refresh_cookie(response: Response) -> None:
    if response is not None:
        response.delete_cookie(
            key="sentinews_refresh_token",
            path=f"{settings.API_V1_STR}/auth",
        )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register New User Account",
)
async def register(
    request: RegisterRequest,
    response: Response,
    use_case: RegisterUseCase = Depends(get_register_use_case),
) -> TokenResponse:
    try:
        res = await use_case.execute(
            email=request.email,
            password=request.password,
            full_name=request.full_name,
        )
        if len(res) == 3:
            user, token, refresh_token = res
        else:
            user, token = res
            refresh_token = None

        _set_refresh_cookie_if_enabled(response, refresh_token)

        return TokenResponse(
            access_token=token,
            refresh_token=refresh_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="User Login (JWT)",
)
async def login(
    request: LoginRequest,
    response: Response,
    use_case: LoginUseCase = Depends(get_login_use_case),
) -> TokenResponse:
    try:
        res = await use_case.execute(
            email=request.email,
            password=request.password,
        )
        if len(res) == 3:
            user, token, refresh_token = res
        else:
            user, token = res
            refresh_token = None

        _set_refresh_cookie_if_enabled(response, refresh_token)

        return TokenResponse(
            access_token=token,
            refresh_token=refresh_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    except InactiveUserError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate Refresh Token & Issue New Access Token",
)
async def refresh_token_endpoint(
    response: Response,
    body: Optional[RefreshRequest] = None,
    cookie_token: Optional[str] = Cookie(None, alias="sentinews_refresh_token"),
    use_case: RefreshUseCase = Depends(get_refresh_use_case),
) -> TokenResponse:
    token_to_use = (body.refresh_token if body and body.refresh_token else None) or cookie_token
    if not token_to_use:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not provided in request body or cookie",
        )

    try:
        user, new_access_token, new_refresh_token = await use_case.execute(token_to_use)
        _set_refresh_cookie_if_enabled(response, new_refresh_token)
        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except RefreshTokenReusedError as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidRefreshTokenError, RefreshTokenExpiredError) as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke Session & Refresh Token",
)
async def logout(
    response: Response,
    body: Optional[LogoutRequest] = None,
    cookie_token: Optional[str] = Cookie(None, alias="sentinews_refresh_token"),
    use_case: LogoutUseCase = Depends(get_logout_use_case),
) -> MessageResponse:
    token_to_revoke = (body.refresh_token if body and body.refresh_token else None) or cookie_token
    await use_case.execute(refresh_token=token_to_revoke)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out successfully", success=True)


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="OAuth2 Access Token (Swagger UI & OAuth2 Clients)",
)
async def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    use_case: LoginUseCase = Depends(get_login_use_case),
) -> TokenResponse:
    try:
        res = await use_case.execute(
            email=form_data.username,
            password=form_data.password,
        )
        if len(res) == 3:
            user, token, refresh_token = res
        else:
            user, token = res
            refresh_token = None

        _set_refresh_cookie_if_enabled(response, refresh_token)

        return TokenResponse(
            access_token=token,
            refresh_token=refresh_token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InactiveUserError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
        )


# =========================================================================
# Google OAuth 2.0 Endpoints
# =========================================================================

@router.get(
    "/google/login",
    response_model=GoogleAuthUrlResponse,
    status_code=status.HTTP_200_OK,
    summary="Initiate Google OAuth Login Flow",
)
async def google_oauth_login(
    redirect: bool = Query(
        False,
        description="If True, directly redirects to Google OAuth consent screen. If False, returns authorization URL as JSON.",
    ),
    redirect_uri: Optional[str] = Query(
        None,
        description="Optional custom OAuth redirect URI (defaults to server configuration).",
    ),
    return_url: Optional[str] = Query(
        None,
        description="Optional client URL to redirect to after successful authentication.",
    ),
    use_case: GoogleLoginUseCase = Depends(get_google_login_use_case),
):
    """
    Initiates Google OAuth 2.0 Authorization Code flow with CSRF state protection.
    """
    try:
        auth_data = use_case.get_authorization_url(redirect_uri=redirect_uri, return_url=return_url)
        if redirect:
            return RedirectResponse(
                url=auth_data["authorization_url"],
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            )
        return GoogleAuthUrlResponse(
            authorization_url=auth_data["authorization_url"],
            state=auth_data["state"],
        )
    except GoogleOAuthNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )


@router.get(
    "/google/callback",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Google OAuth Callback Handler",
)
async def google_oauth_callback(
    code: Optional[str] = Query(None, description="Google authorization code"),
    state: Optional[str] = Query(None, description="Signed CSRF state parameter"),
    error: Optional[str] = Query(None, description="OAuth error code returned by Google"),
    error_description: Optional[str] = Query(None, description="OAuth error description"),
    use_case: GoogleLoginUseCase = Depends(get_google_login_use_case),
) -> TokenResponse:
    """
    Handles Google OAuth redirect with authorization code and state token.
    Validates CSRF state, exchanges code for user profile, and returns SentiNews JWT session.
    """
    if error:
        detail = error_description or error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google OAuth authorization failed: {detail}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required 'code' or 'state' parameters in OAuth callback",
        )

    try:
        user, token, _ = await use_case.handle_callback(code=code, state=state)
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except GoogleOAuthNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )
    except GoogleAuthFailedError as e:
        # Check if CSRF failure
        if "CSRF" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


@router.post(
    "/google/exchange",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Direct Google OAuth Code Exchange (SPAs & Mobile)",
)
async def google_oauth_exchange(
    request: GoogleExchangeRequest,
    use_case: GoogleLoginUseCase = Depends(get_google_login_use_case),
) -> TokenResponse:
    """
    Exchanges an authorization code directly for applications handling the redirect on the frontend.
    """
    try:
        user, token = await use_case.execute_code(
            code=request.code,
            redirect_uri=request.redirect_uri,
        )
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except GoogleOAuthNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )
    except GoogleAuthFailedError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


@router.post(
    "/google",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Google ID Token Login (Google One-Tap / Mobile / NextAuth)",
)
async def google_login(
    request: GoogleAuthRequest,
    use_case: GoogleLoginUseCase = Depends(get_google_login_use_case),
) -> TokenResponse:
    """
    Verifies a Google ID Token (credential) and issues a SentiNews JWT session.
    """
    try:
        user, token = await use_case.execute_id_token(id_token=request.id_token)
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
            ),
        )
    except GoogleOAuthNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )
    except GoogleAuthFailedError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


# =========================================================================
# Protected User Endpoint
# =========================================================================

@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Authenticated User",
)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
    )
