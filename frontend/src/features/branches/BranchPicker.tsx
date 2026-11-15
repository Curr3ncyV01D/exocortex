import type { BranchResponse } from '../../api/types'

interface BranchPickerProps {
  branches: BranchResponse[]
  activeBranchId: string
  onSelect: (branchId: string) => void
}

export function BranchPicker({ branches, activeBranchId, onSelect }: BranchPickerProps) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-sm text-muted">
      Ветка
      <select
        className="field min-w-0 max-w-full md:max-w-56"
        value={activeBranchId}
        onChange={(event) => onSelect(event.target.value)}
      >
        {branches.map((branch) => (
          <option key={branch.id} value={branch.id}>{branch.name}</option>
        ))}
      </select>
    </label>
  )
}
