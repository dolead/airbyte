import http
import json
import sys

import backoff
from facebook_business import FacebookAdsApi
from facebook_business.exceptions import FacebookRequestError
from source_facebook_marketing.api import MyFacebookAdsApi, backoff_policy

# Standard library imports...
from unittest.mock import patch, MagicMock
import logging

from source_facebook_marketing.streams.common import FACEBOOK_RATE_LIMIT_ERROR_CODES, FACEBOOK_TEMPORARY_OAUTH_ERROR_CODE, \
    FACEBOOK_BATCH_ERROR_CODE, FACEBOOK_UNKNOWN_ERROR_CODE, FACEBOOK_CONNECTION_RESET_ERROR_CODE

logger = logging.getLogger("airbyte")

FB_API_VERSION = FacebookAdsApi.API_VERSION


def retry_pattern(backoff_type, exception, **wait_gen_kwargs):
    def log_retry_attempt(details):
        _, exc, _ = sys.exc_info()
        logger.info(str(exc))
        logger.info(f"Caught retryable error after {details['tries']} tries. Waiting {details['wait']} more seconds then retrying...")

    def choose_strategy(details):
        _, exc, _ = sys.exc_info()
        tries = details.get("tries")
        code = exc._error.get("code")
        if code == 1 and tries >= 4:
            switch_cursor(details)
        elif "Cannot include" in exc.api_error_message or "does not exist" in exc.api_error_message:
            switch_cursor(details)
        else:
            reduce_request_record_limit(details)

    def switch_cursor(details):
        _, exc, _ = sys.exc_info()
        del details["kwargs"]["params"]["after"]

    def reduce_request_record_limit(details):
        _, exc, _ = sys.exc_info()
        # the list of error patterns to track,
        # in order to reduce the request page size and retry
        error_patterns = [
            "Please reduce the amount of data you're asking for, then retry your request",
            "An unknown error occurred",
            'An unknown error has occurred.'
        ]
        if (
                details.get("kwargs", {}).get("params", {}).get("limit")
                and exc.http_status() == http.client.INTERNAL_SERVER_ERROR
                and exc.api_error_message() in error_patterns
        ):
            # reduce the existing request `limit` param by a half and retry
            details["kwargs"]["params"]["limit"] = int(int(details["kwargs"]["params"]["limit"]) / 2)
            # set the flag to the api class that the last api call failed
            details.get("args")[0].last_api_call_is_successfull = False
            # set the flag to the api class that the `limit` param was reduced
            details.get("args")[0].request_record_limit_is_reduced = True

    def revert_request_record_limit(details):
        """
        This method is triggered `on_success` after successfull retry,
        sets the internal class flags to provide the logic to restore the previously reduced
        `limit` param.
        """
        # reference issue: https://github.com/airbytehq/airbyte/issues/25383
        # set the flag to the api class that the last api call was ssuccessfull
        pass

    def should_retry_api_error(exc):
        if isinstance(exc, FacebookRequestError):
            call_rate_limit_error = exc.api_error_code() in FACEBOOK_RATE_LIMIT_ERROR_CODES
            temporary_oauth_error = exc.api_error_code() == FACEBOOK_TEMPORARY_OAUTH_ERROR_CODE
            batch_timeout_error = exc.http_status() == http.client.BAD_REQUEST and exc.api_error_code() == FACEBOOK_BATCH_ERROR_CODE
            unknown_error = exc.api_error_subcode() == FACEBOOK_UNKNOWN_ERROR_CODE
            connection_reset_error = exc.api_error_code() == FACEBOOK_CONNECTION_RESET_ERROR_CODE
            server_error = exc.http_status() == http.client.INTERNAL_SERVER_ERROR
            missing_permissions = "Cannot include" in exc._message
            return any(
                (
                    exc.api_transient_error(),
                    unknown_error,
                    call_rate_limit_error,
                    batch_timeout_error,
                    connection_reset_error,
                    temporary_oauth_error,
                    server_error,
                    missing_permissions
                )
            )
        return True

    return backoff.on_exception(
        backoff_type,
        exception,
        jitter=None,
        on_backoff=[log_retry_attempt, choose_strategy],
        on_success=[revert_request_record_limit],
        giveup=lambda exc: not should_retry_api_error(exc),
        **wait_gen_kwargs,
    )


def test_retry_pattern_error_33():
    backoff_policy = retry_pattern(backoff_type=backoff.constant, exception=FacebookRequestError)

    with open("facebook_request_context.json") as file:
        facebook_request_context = json.loads(file.read())
    with open("faccebook_http_headers.json") as file:
        facebook_http_headers = json.loads(file.read())
    http_status = 400
    message = "(#100) Cannot include account_currency in summary param because they weren't there while creating the report run"

    err = FacebookRequestError(message=message,
                               request_context=facebook_request_context,
                               http_status=http_status,
                               http_headers=facebook_http_headers,
                               body={})

    err._error = {"code": 1}

    @backoff_policy
    def call():
        raise err

    test = call()
