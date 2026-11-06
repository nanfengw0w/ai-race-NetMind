const STORAGE_KEY = 'netmind_notes'

function loadNotes() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveNotes(notes) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(notes))
}

export function getNotes() {
  return loadNotes()
}

export function addNote(note) {
  const notes = loadNotes()
  const entry = {
    id: `note_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    createdAt: new Date().toISOString(),
    ...note,
  }
  notes.unshift(entry)
  saveNotes(notes)
  return entry
}

export function deleteNote(id) {
  const notes = loadNotes().filter(n => n.id !== id)
  saveNotes(notes)
  return notes
}

export function addTextNote(text, source = '') {
  return addNote({
    type: 'text',
    content: text.trim(),
    source,
  })
}

export function addQuizNote(question, options, answer, explanation) {
  return addNote({
    type: 'quiz',
    content: question,
    options: Array.isArray(options) ? options : [],
    answer: answer || '',
    explanation: explanation || '',
  })
}

export function addBookmarkNote(title, html) {
  return addNote({
    type: 'bookmark',
    content: title,
    html: html || '',
  })
}
