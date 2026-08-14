# Plan — Fix Server Filtering & Live Utilisation Selection

This plan addresses the issue where selecting a server (e.g., a Hyper-V server) does not properly filter the VM inventory list (still showing VMware ESXi and other VMs), and enables the ability to select/filter by a server directly from the "Live Host Utilisation" tab.

## Top-Level Overview
The user is selecting a server from the "Live Host Utilisation" cards (e.g., checking a server card), but currently, this action only selects the server for CSV export (`selectedIds`) and does not update the centralized `filters` state. As a result, the VM Inventory list is not filtered, and VMs from other servers (like VMware ESXi) are still shown. 

To fix this and provide an intuitive user experience:
1. **Enable Card-Based Filtering**: We will make the server cards in the "Live Host Utilisation" tab clickable. Clicking a server card will toggle it as the active VM filter (setting both `filters.hypervisorType` and `filters.serverId`).
2. **Sync Dropdowns and Badges**: This card-based selection will automatically sync the Level 1 and Level 2 dropdowns, active filter pills, and VM table.
3. **Visual Active State**: We will style the active filtered server card with a distinct blue border, ring, and "Active Filter" badge so the user has immediate visual feedback of which host is filtering the VMs.
4. **Prevent Event Bubbling**: We will stop event propagation on the server card's checkbox and drive list toggles so that checking a server for export or expanding drives does not trigger the VM filter toggle.

---

## Sub-Tasks

### 1. Update `ServerCard` Component to Support Filtering
- **Intent**: Allow `ServerCard` to respond to clicks and display an active filtered state.
- **Expected Outcomes**:
  - `ServerCard` accepts `isFilterActive` (boolean) and `onCardClick` (function) props.
  - Hovering over a card shows a pointer cursor and a tooltip ("Click to filter VMs by this server" or "Click to remove filter").
  - An "Active Filter" badge is displayed in the card header when `isFilterActive` is true.
  - The card displays a prominent blue ring/border when `isFilterActive` is true.
  - Clicking the export checkbox or "show drives" button does not bubble up to the card click handler.
- **Todo List**:
  - [ ] Add `isFilterActive` and `onCardClick` props to `ServerCard` definition in `monitoring-app/frontend/src/Dashboard.jsx`.
  - [ ] Update card container className to support conditional borders/rings/backgrounds based on `isFilterActive` and cursor pointer.
  - [ ] Add `onClick={onCardClick}` and `title={...}` attributes to the outer container.
  - [ ] Add `e.stopPropagation()` to the export checkbox `onClick` handler.
  - [ ] Add `e.stopPropagation()` to the "show drives" button `onClick` handler.
  - [ ] Add the "Filter" badge next to the server display name when `isFilterActive` is true.
- **Relevant Context**:
  - File: `monitoring-app/frontend/src/Dashboard.jsx` (lines 249-347)
- **Status**: `[ ] pending`

### 2. Implement Card Click Filter Handler and Integrate in Dashboard
- **Intent**: Connect the clicked server card to the centralized `filters` state in `Dashboard`.
- **Expected Outcomes**:
  - Clicking a server card updates `filters.serverId` and `filters.hypervisorType`.
  - Clicking an already-selected server card toggles/clears the server filter.
  - The select dropdowns, filter pills, and VM Table immediately refresh and filter correctly.
- **Todo List**:
  - [ ] Define `handleServerCardClick` in the `Dashboard` component in `monitoring-app/frontend/src/Dashboard.jsx`.
  - [ ] In the handler, if `filters.serverId === server.server_id`, clear the server filter (`serverId: ""`). Otherwise, set `serverId` and `hypervisorType` to the card's values.
  - [ ] Pass `isFilterActive={filters.serverId === s.server_id}` and `onCardClick={() => handleServerCardClick(s)}` props to the rendered `ServerCard`s in the "Live Host Utilisation" section.
- **Relevant Context**:
  - File: `monitoring-app/frontend/src/Dashboard.jsx` (around lines 820-830 and lines 1290-1298)
- **Status**: `[ ] pending`

### 3. Verify and Validate
- **Intent**: Verify that selecting any server from the dropdowns OR by clicking its card in "Live Host Utilisation" correctly filters the VM Inventory list to only show VMs belonging to that host.
- **Expected Outcomes**:
  - Selecting a Hyper-V server filters the list to show only Hyper-V VMs from that host.
  - Selecting a VMware ESXi server filters the list to show only ESXi VMs from that host.
  - No ESXi VMs are displayed when a Hyper-V server is selected.
  - Clearing the filter displays all VMs correctly.
- **Todo List**:
  - [ ] Test the dropdown level 1 and level 2 filters.
  - [ ] Test clicking server cards under "Live Host Utilisation" to filter and untoggle.
  - [ ] Test that checkboxes and drive expanders still function independently without toggling the filter.
- **Relevant Context**:
  - Dashboard filters and VM Inventory table.
- **Status**: `[ ] pending`
