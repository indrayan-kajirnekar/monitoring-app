# Plan — Display Snapshot Count Instead of Status in VM Inventory

This plan replaces the "Status" column in the VM Inventory with a "Snapshots" column that correctly displays the number of snapshots per VM.

## Top-Level Overview
1. **Backend Integration**: 
   - Add `snapshot_count: int` to the `VMRecord` Pydantic model.
   - In the `/api/vms` route handler, query the database's `vm_snapshot` table using SQLAlchemy to retrieve snapshot counts grouped by `vm_id`.
   - Populate `snapshot_count` for each returned `VMRecord` object.
2. **Frontend UI Transformation**:
   - In `Dashboard.jsx`, change the VM Inventory table column header from "Status" to "Snapshots".
   - In the VM Inventory table body, replace the rendering of `StatusDot` with the display of the VM's snapshot count (styled cleanly with a badge or simple numeric text).
   - In `VMTable`'s sorting logic, replace status-based sorting with snapshot count-based sorting so the VM list can be sorted by the number of snapshots.

---

## Sub-Tasks

### 1. Update Backend `VMRecord` and `/api/vms`
- **Intent**: Populate and expose snapshot count for each VM on the backend.
- **Expected Outcomes**:
  - `VMRecord` Pydantic model includes a `snapshot_count: int` field.
  - The `get_vms` route handler retrieves grouped snapshot counts from `vm_snapshot` and populates `snapshot_count` in each `VMRecord`.
- **Todo List**:
  - [ ] Add `snapshot_count: int` to `class VMRecord(BaseModel)` in `monitoring-app/backend/main.py`.
  - [ ] Query and group snapshot counts by `vm_id` using SQLAlchemy `func.count` in `get_vms`.
  - [ ] Map the snapshot count to each `VMRecord` during instantiating.
- **Relevant Context**:
  - Backend files: `monitoring-app/backend/main.py`
- **Status**: `[ ] pending`

### 2. Update Frontend `VMTable` UI and Sorting
- **Intent**: Display and sort the VM inventory table by snapshot count instead of status.
- **Expected Outcomes**:
  - Column header text is changed from "Status" to "Snapshots" (using `col="snapshot_count"`).
  - The cell renders `vm.snapshot_count` correctly (e.g. styled as a clean badge or text, like "3 snaps" or "—" for 0).
  - Clicking the "Snapshots" column header correctly sorts the table by snapshot counts.
- **Todo List**:
  - [ ] Replace the column header `<Th label="Status" col="status" />` with `<Th label="Snapshots" col="snapshot_count" />`.
  - [ ] Replace the table cell `<StatusDot status={vm.status} />` with a display of `vm.snapshot_count` (e.g., `<span className="inline-block px-2 py-0.5 rounded text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">{vm.snapshot_count} snaps</span>` or a similar neat layout).
- **Relevant Context**:
  - Frontend files: `monitoring-app/frontend/src/Dashboard.jsx` (specifically inside `VMTable` component)
- **Status**: `[ ] pending`

### 3. Verify and Validate
- **Intent**: Verify that snapshot count is correctly retrieved and displayed for each VM, and sorting by snapshots works without regressions.
- **Expected Outcomes**:
  - The "Snapshots" column is visible in the VM Inventory table.
  - Snapshot counts match the actual number of snapshots.
  - Clicking "Snapshots" header sorts VMs by count ascending/descending.
- **Todo List**:
  - [ ] Verify the table rendering.
  - [ ] Verify the sorting.
- **Status**: `[ ] pending`
