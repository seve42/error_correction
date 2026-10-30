import { reactive, ref } from 'vue'
import type { Id } from '@/types/domain'

/**
 * useBatchSelection — 通用批量选择 composable
 *
 * 错题库、导入核对页、分割历史等列表共用：
 * 维护选中集合，支持跨页「全选所有筛选结果」与清空。
 */
export function useBatchSelection() {
  const selectedIds = reactive(new Set<Id>())
  const selecting = ref(false)

  /** 切换单个项目的选中状态 */
  const toggleSelect = (id: Id) => {
    selectedIds.has(id) ? selectedIds.delete(id) : selectedIds.add(id)
  }

  /** 用给定 ID 列表整体替换选中集合 */
  const selectIds = (ids: Id[]) => {
    selectedIds.clear()
    ids.forEach((id) => selectedIds.add(id))
  }

  /** 清空选中 */
  const clearSelection = () => {
    selectedIds.clear()
  }

  /** 通过异步加载器选中全部（支持跨页/跨筛选结果） */
  const selectAllMatching = async (loader: () => Promise<Id[]>) => {
    if (selecting.value) return
    selecting.value = true
    try {
      selectIds(await loader())
    } finally {
      selecting.value = false
    }
  }

  return {
    selectedIds,
    selecting,
    toggleSelect,
    selectIds,
    clearSelection,
    selectAllMatching,
  }
}
