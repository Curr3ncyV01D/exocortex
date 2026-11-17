const timestamp = '2026-11-15T10:00:00Z'

export const chat = {
  id: 'chat-1', title: 'Первый чат', created_at: timestamp, updated_at: timestamp,
}

export const main = {
  id: 'branch-main', chat_id: chat.id, name: 'main', head_message_id: 'merge',
  forked_from_id: null, last_merged_message_id: null, status: 'active', created_at: timestamp,
}

export const side = {
  ...main, id: 'branch-side', name: 'feature/refactor', head_message_id: null,
}

const message = {
  chat_id: chat.id, is_active: true, is_edited: false, metadata: null,
  created_at: timestamp, updated_at: null,
}

export const timeline = [
  { ...message, id: 'question', parent_id: null, role: 'user', content: 'Вопрос' },
  {
    ...message, id: 'masked', parent_id: 'question', role: 'model', is_active: false,
    content: '**Скрытый ответ**\n\n\`\`\`python\nprint(1)\n\`\`\`',
  },
  { ...message, id: 'merge', parent_id: 'masked', role: 'system_merge', content: 'Итог ветки' },
]
