# Remove Collection Max Reason Rounds Design

## Context

`collection_max_reason_rounds` exists as a project create field, project response field, SQLite column, YAML export field, UI input, and a small set of tests. The server no longer uses it to stop or throttle collection reason scheduling. Keeping it exposed suggests a behavior that does not exist.

## Decision

Remove `collection_max_reason_rounds` completely from active code paths:

- Do not accept it in `POST /projects`.
- Do not include it in project API responses.
- Do not include it in YAML export.
- Do not show or send it from the browser UI.
- Do not include it in the current SQLite `projects` schema.
- Remove tests and prompt fixtures that treat `max_reason_rounds` as a required collection field.

## Architecture

The only collection round state kept on projects is the actual counters: `collection_reason_rounds`, `collection_explore_rounds`, and `collection_stable_rounds`. These counters remain visible in API responses, YAML export, and the UI. Collection scheduling remains controlled by current dispatcher logic, not by a per-project max round setting.

## Data Migration

New databases should be created without `projects.collection_max_reason_rounds`. Existing databases that still contain the column should be rebuilt through the existing migration path so the column is dropped while preserving all other project fields.

Legacy `recon_max_reason_rounds` is not copied into a replacement field because there is no replacement setting.

## API And Export Behavior

`CreateProjectRequest` forbids unknown fields, so requests that still send `collection_max_reason_rounds` should fail with the existing validation behavior. Project response models omit the field. YAML export `collection` omits `max_reason_rounds` and keeps the remaining round counters and judge metadata.

## UI Behavior

The New Project modal no longer asks for a collection max. The collection coverage summary shows the actual reason round count without a `/max` suffix.

## Testing

Update API, integration, and fixture tests so they assert the absence of the field and continue to verify collection counters. Run targeted tests covering project creation, YAML export, mock scheduling, and prompt fixture shape.
