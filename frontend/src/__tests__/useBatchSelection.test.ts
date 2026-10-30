/**
 * 批量选择组合式函数测试。
 * 覆盖 useBatchSelection 的单个切换、整体替换、清空与异步全选。
 */
import { describe, it, expect } from 'vitest'
import { useBatchSelection } from '../composables/useBatchSelection'

describe('useBatchSelection', () => {
  it('toggleSelect 切换单个 ID', () => {
    const { selectedIds, toggleSelect } = useBatchSelection()
    toggleSelect(1)
    expect(selectedIds.has(1)).toBe(true)
    toggleSelect(1)
    expect(selectedIds.has(1)).toBe(false)
  })

  it('selectIds 用新列表替换选中集合', () => {
    const { selectedIds, selectIds } = useBatchSelection()
    selectIds([1, 2, 3])
    selectIds([4])
    expect([...selectedIds]).toEqual([4])
  })

  it('clearSelection 清空选中', () => {
    const { selectedIds, selectIds, clearSelection } = useBatchSelection()
    selectIds([1, 2])
    clearSelection()
    expect(selectedIds.size).toBe(0)
  })

  it('selectAllMatching 通过异步加载器跨页选中全部', async () => {
    const { selectedIds, selecting, selectAllMatching } = useBatchSelection()
    const loading = selectAllMatching(async () => [7, 8, 9])
    expect(selecting.value).toBe(true)
    await loading
    expect(selecting.value).toBe(false)
    expect([...selectedIds]).toEqual([7, 8, 9])
  })
})
