/**
 * Application shell.
 *
 * Six screens following the pipeline: Sources puts files into the index,
 * Chunking is where different ways of cutting those files get compared, Chat
 * asks questions of them, Evaluations is where those answers get judged against
 * the chunks that produced them, Golden Sets is where the answer keys those
 * scores are measured against get drafted and signed off, and Prompts is the
 * wording all of it was written under. Six screens do not justify a router.
 *
 * Two pieces of state cross screens, and both exist for the same reason —
 * following a link should not mean re-selecting what you just clicked. Pressing
 * Ask on a chunking variant opens Chat pointed at it, and clicking where a file
 * is indexed on Sources opens Chunking on that file.
 */

import { useState } from 'react'

import { ChatView } from './features/chat/ChatView'
import { ChunkingView } from './features/chunking/ChunkingView'
import { GoldenView } from './features/golden/GoldenView'
import { PromptsView } from './features/prompts/PromptsView'
import { SourcesView } from './features/sources/SourcesView'
import { TracesView } from './features/traces/TracesView'

type Tab = 'sources' | 'chunking' | 'chat' | 'evaluations' | 'golden' | 'prompts'

const TABS: { value: Tab; label: string }[] = [
  { value: 'sources', label: 'Sources' },
  { value: 'chunking', label: 'Chunking' },
  { value: 'chat', label: 'Chat' },
  { value: 'evaluations', label: 'Evaluations' },
  { value: 'golden', label: 'Golden Sets' },
  { value: 'prompts', label: 'Prompts' },
]

function App() {
  const [tab, setTab] = useState<Tab>('sources')
  // The variant Chat should open with, set by the Chunking tab's Ask button.
  const [askVariant, setAskVariant] = useState('')
  // The file Chunking should open on, set by a chip on the Sources tab.
  const [benchSource, setBenchSource] = useState('')

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
        {tab === 'sources' ? (
          <SourcesView
            onOpenVariant={(sourceKey) => {
              setBenchSource(sourceKey)
              setTab('chunking')
            }}
          />
        ) : null}
        {tab === 'chunking' ? (
          <ChunkingView
            openSource={benchSource}
            onAsk={(variantId) => {
              setAskVariant(variantId)
              setTab('chat')
            }}
          />
        ) : null}
        {tab === 'chat' ? <ChatView initialVariant={askVariant} /> : null}
        {tab === 'evaluations' ? <TracesView /> : null}
        {tab === 'golden' ? <GoldenView /> : null}
        {tab === 'prompts' ? <PromptsView /> : null}
      </main>
    </div>
  )
}

export default App
