const clock = new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' })
const calendarDate = new Intl.DateTimeFormat('ru', { dateStyle: 'medium' })

export function editedTitle(updatedAt: string | null): string {
  if (!updatedAt) return 'Реплика отредактирована'
  const edited = new Date(updatedAt)
  return `Реплика отредактирована в ${clock.format(edited)} (${calendarDate.format(edited)})`
}
