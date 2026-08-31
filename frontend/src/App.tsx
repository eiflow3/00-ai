/**
 * Application shell.
 *
 * Five screens following the pipeline: Sources puts files into the index, Chat
 * asks questions of it, Evaluations is where those answers get judged against
 * the chunks that produced them, Golden Sets is where the answer keys those
 * scores are measured against get drafted and signed off, and Prompts is the
 * wording all of it was written under. Five screens do not justify a router.
 */

import { useState } from 'react'

import { ChatView } from './features/chat/ChatView'
import { GoldenView } from './features/golden/GoldenView'
import { PromptsView } from './features/prompts/PromptsView'
import { SourcesView } from './features/sources/SourcesView'
import { TracesView } from './features/traces/TracesView'

type Tab = 'sources' | 'chat' | 'evaluations' | 'golden' | 'prompts'

const TABS: { value: Tab; label: string }[] = [
  { value: 'sources', label: 'Sources' },
  { value: 'chat', label: 'Chat' },
  { value: 'evaluations', label: 'Evaluations' },
  { value: 'golden', label: 'Golden Sets' },
  { value: 'prompts', label: 'Prompts' },
]

function App() {
  const [tab, setTab] = useState<Tab>('sources')

  return (
    <div className="min-h-screen bg-slate-50">
      <nav className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-1 px-6">
          {TABS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setTab(option.value)}
              aria-current={tab === option.value ? 'page' : undefined}
              className={`-mb-px border-b-2 px-3 py-3 text-sm font-medium ${
                tab === option.value
                  ? 'border-slate-900 text-slate-900'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </nav>

      <main>
        {tab === 'sources' ? <SourcesView /> : null}
        {tab === 'chat' ? <ChatView /> : null}
        {tab === 'evaluations' ? <TracesView /> : null}
        {tab === 'golden' ? <GoldenView /> : null}
        {tab === 'prompts' ? <PromptsView /> : null}
      </main>
    </div>
  )
}

export default App
