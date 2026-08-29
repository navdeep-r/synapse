/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommunityReport } from '../models/CommunityReport';
import type { EnterpriseAPIRequest } from '../models/EnterpriseAPIRequest';
import type { EnterpriseAPIResponse } from '../models/EnterpriseAPIResponse';
import type { ResolvedEntity } from '../models/ResolvedEntity';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * Generate Snapshot
     * @param requestBody
     * @returns EnterpriseAPIResponse Successful Response
     * @throws ApiError
     */
    public static generateSnapshotApiV1SnapshotGeneratePost(
        requestBody: EnterpriseAPIRequest,
    ): CancelablePromise<EnterpriseAPIResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/snapshot/generate',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Entity
     * @param entityId
     * @returns ResolvedEntity Successful Response
     * @throws ApiError
     */
    public static getEntityApiV1EntitiesEntityIdGet(
        entityId: string,
    ): CancelablePromise<ResolvedEntity> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/entities/{entity_id}',
            path: {
                'entity_id': entityId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Report
     * @param reportId
     * @returns CommunityReport Successful Response
     * @throws ApiError
     */
    public static getReportApiV1ReportsReportIdGet(
        reportId: string,
    ): CancelablePromise<CommunityReport> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/reports/{report_id}',
            path: {
                'report_id': reportId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
