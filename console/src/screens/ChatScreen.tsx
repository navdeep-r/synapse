import * as React from 'react';
import {
  Send,
  Bot,
  User,
  BookOpen,
  Sparkles,
  ChevronDown,
  AlertCircle,
  Network
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { motion } from 'framer-motion';
import { api, type ChatMessage, type CitedFact } from '../api/client';

// ── Types -------------------------------------------------------------------

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: CitedFact[];
  factsUsed?: number;
  loading?: boolean;
  error?: boolean;
}

// ── Helpers -----------------------------------------------------------------

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

function preprocessMarkdown(content: string): string {
  if (!content) return '';
  return content.replace(/\r\n/g, '\n').trim();
}

// ── Sub-components ----------------------------------------------------------

function CitationCard({ fact }: { fact: CitedFact }) {
  const [open, setOpen] = React.useState(false);

  return (
    <div className="border border-hairline/60 rounded-lg overflow-hidden text-xs bg-white shadow-sm mt-1">
      <button
        type="button"
        className="w-full flex items-start gap-2 px-3 py-2.5 hover:bg-paper/50 transition-colors text-left"
        onClick={() => setOpen(current => !current)}
        aria-expanded={open}
      >
        <BookOpen className="h-3.5 w-3.5 mt-0.5 text-ledger shrink-0" />
        <span className="flex-1 text-ink-muted leading-relaxed line-clamp-2">
          {fact.fact}
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 text-ink-muted shrink-0 transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>

      {open && (
        <div className="px-3 pb-3 bg-paper/30 border-t border-hairline/60 space-y-1.5 pt-2">
          {fact.source_name && fact.target_name && (
            <div className="text-ink-muted flex items-center flex-wrap gap-1.5">
              <span className="font-medium text-ink bg-white px-1.5 py-0.5 rounded border border-hairline shadow-sm">
                {fact.source_name}
              </span>
              {fact.relation_type && (
                <span className="text-[10px] uppercase tracking-wider text-ledger font-semibold">
                  {fact.relation_type}
                </span>
              )}
              <span className="font-medium text-ink bg-white px-1.5 py-0.5 rounded border border-hairline shadow-sm">
                {fact.target_name}
              </span>
            </div>
          )}
          <p className="text-ink leading-relaxed mt-2">
            {fact.fact}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Assistant bubble --------------------------------------------------------

function AssistantBubble({ msg }: { msg: Message }) {
  const [showCitations, setShowCitations] = React.useState(false);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex gap-4 w-full"
    >
      <div className="h-8 w-8 rounded-lg bg-ledger/10 border border-ledger/20 flex items-center justify-center shrink-0 mt-1">
        <Sparkles className="h-4 w-4 text-ledger" />
      </div>

      <div className="flex-1 min-w-0 space-y-3 pt-1">
        {msg.loading ? (
          <div className="flex items-center gap-2 text-ink-muted h-6">
            <span className="flex gap-1">
              {[0, 1, 2].map(i => (
                <span
                  key={i}
                  className="h-1.5 w-1.5 bg-ledger/60 rounded-full animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }}
                />
              ))}
            </span>
            <span className="text-sm font-medium animate-pulse">Searching knowledge graph...</span>
          </div>
        ) : msg.error ? (
          <div className="bg-alert/5 border border-alert/20 rounded-xl px-4 py-3 flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-alert shrink-0 mt-0.5" />
            <p className="text-sm text-alert-dark font-medium leading-relaxed">
              {msg.content}
            </p>
          </div>
        ) : (
          <>
            <div className="prose prose-sm sm:prose-base max-w-none text-ink prose-p:leading-relaxed prose-pre:bg-paper prose-pre:border prose-pre:border-hairline prose-headings:font-semibold prose-a:text-ledger hover:prose-a:text-ledger-dark">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  table: ({ children }) => (
                    <div className="not-prose w-full overflow-x-auto my-6 rounded-xl border border-hairline/80 shadow-sm bg-white">
                      <table className="w-full text-sm text-left">
                        {children}
                      </table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-paper border-b border-hairline/80 text-ink font-semibold">
                      {children}
                    </thead>
                  ),
                  tbody: ({ children }) => (
                    <tbody className="divide-y divide-hairline/40">
                      {children}
                    </tbody>
                  ),
                  tr: ({ children }) => (
                    <tr className="hover:bg-paper/30 transition-colors">
                      {children}
                    </tr>
                  ),
                  th: ({ children }) => (
                    <th className="px-4 py-3 whitespace-nowrap">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="px-4 py-3 align-top">
                      {children}
                    </td>
                  ),
                }}
              >
                {preprocessMarkdown(msg.content)}
              </ReactMarkdown>
            </div>

            {msg.citations && msg.citations.length > 0 && (
              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => setShowCitations(current => !current)}
                  className="inline-flex items-center gap-1.5 text-xs text-ledger hover:text-ledger-dark hover:bg-ledger/5 px-2.5 py-1.5 rounded-md transition-colors font-medium border border-transparent hover:border-ledger/20"
                  aria-expanded={showCitations}
                >
                  <Network className="h-3.5 w-3.5" />
                  {msg.factsUsed ?? msg.citations.length} facts grounded
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ${showCitations ? 'rotate-180' : ''}`} />
                </button>

                {showCitations && (
                  <motion.div 
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    className="space-y-2 mt-3 pl-1"
                  >
                    {msg.citations.map((citation, index) => (
                      <CitationCard key={`${citation.fact}-${index}`} fact={citation} />
                    ))}
                  </motion.div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </motion.div>
  );
}

// ── User bubble -------------------------------------------------------------

function UserBubble({ msg }: { msg: Message }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex gap-4 w-full"
    >
      <div className="h-8 w-8 rounded-lg bg-ink text-white flex items-center justify-center shrink-0 mt-1 shadow-sm">
        <User className="h-4 w-4" />
      </div>

      <div className="flex-1 min-w-0 pt-1.5">
        <p className="text-body font-medium text-ink whitespace-pre-wrap leading-relaxed">
          {msg.content}
        </p>
      </div>
    </motion.div>
  );
}

// ── Main screen -------------------------------------------------------------

export default function ChatScreen() {
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [input, setInput] = React.useState('');
  const [sending, setSending] = React.useState(false);

  const bottomRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const scrollAreaRef = React.useRef<HTMLDivElement>(null);

  // Auto-scroll to the latest message.
  React.useEffect(() => {
    // Only scroll if we are near the bottom to avoid fighting the user's scroll
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  async function sendMessage(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || sending) return;

    const history: ChatMessage[] = messages
      .filter(message => !message.loading)
      .map(message => ({ role: message.role, content: message.content }));

    history.push({ role: 'user', content: text });

    const userMsg: Message = { id: uid(), role: 'user', content: text };
    const loadingMsg: Message = { id: uid(), role: 'assistant', content: '', loading: true };

    setInput('');
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'; // reset height
    }
    setMessages(prev => [...prev, userMsg, loadingMsg]);
    setSending(true);

    try {
      const response = await api.chat({
        messages: history,
        num_facts: 20,
      });

      setMessages(prev =>
        prev.map(message =>
          message.id === loadingMsg.id
            ? {
                ...message,
                content: response.answer,
                citations: response.citations ?? [],
                factsUsed: response.facts_used ?? response.citations?.length ?? 0,
                loading: false,
                error: false,
              }
            : message
        )
      );
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'Something went wrong. Please try again.';
      setMessages(prev =>
        prev.map(message =>
          message.id === loadingMsg.id
            ? {
                ...message,
                content: errorMessage,
                loading: false,
                error: true,
              }
            : message
        )
      );
    } finally {
      setSending(false);
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      });
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  const starters = [
    'What are the key entities in the knowledge graph?',
    'Which files have open issues or pull requests?',
    'Who are the main contributors and what did they work on?',
    'What are the main dependencies in this codebase?',
  ];

  return (
    <div className="flex flex-col flex-1 min-h-0 w-full bg-paper relative">
      {/* Header */}
      <header className="shrink-0 border-b border-hairline/60 bg-paper/95 backdrop-blur-md z-10 sticky top-0 py-4 px-4 sm:px-8">
        <div className="w-full flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-ink tracking-tight flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-ledger shrink-0" />
              <span>Knowledge Chat</span>
            </h1>
          </div>
        </div>
      </header>

      {/* Messages area */}
      <div 
        ref={scrollAreaRef}
        className="flex-1 overflow-y-auto scroll-smooth w-full"
      >
        <div className="max-w-4xl mx-auto w-full px-4 sm:px-8 py-8 sm:py-12 flex flex-col min-h-full">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center flex-1 text-center py-10 mt-[-5%]">
              <div className="h-16 w-16 rounded-2xl bg-gradient-to-br from-ledger/10 to-ledger/5 border border-ledger/20 text-ledger flex items-center justify-center mb-6 shadow-sm">
                <Bot className="h-8 w-8" />
              </div>

              <h2 className="text-2xl sm:text-3xl font-semibold text-ink tracking-tight mb-3">
                Graph-Grounded Assistant
              </h2>

              <p className="text-body text-ink-muted max-w-lg mb-12 leading-relaxed">
                I answer questions using only the facts extracted from your knowledge graph. No hallucinations — every answer is backed by real data.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
                {starters.map(starter => (
                  <button
                    key={starter}
                    type="button"
                    onClick={() => void sendMessage(starter)}
                    className="group flex flex-col items-start p-4 text-left border border-hairline/60 rounded-xl bg-white shadow-sm hover:shadow-md hover:border-ledger/40 transition-all duration-200"
                  >
                    <span className="text-sm font-medium text-ink group-hover:text-ledger-dark transition-colors line-clamp-2">
                      {starter}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-col space-y-8 sm:space-y-10 pb-4">
              {messages.map(message =>
                message.role === 'user' ? (
                  <UserBubble key={message.id} msg={message} />
                ) : (
                  <AssistantBubble key={message.id} msg={message} />
                )
              )}
              <div ref={bottomRef} className="h-1" />
            </div>
          )}
        </div>
      </div>

      {/* Input area */}
      <div className="shrink-0 bg-gradient-to-t from-paper via-paper to-transparent pt-2 pb-6 px-4 sm:px-8 z-10 w-full">
        <div className="max-w-4xl mx-auto w-full">
          <div className="relative border border-hairline/80 shadow-[0_2px_12px_rgba(0,0,0,0.04)] rounded-2xl bg-white focus-within:border-ledger/50 focus-within:ring-2 focus-within:ring-ledger/20 transition-all flex items-end">
            <textarea
              ref={inputRef}
              id="chat-input"
              rows={1}
              className="w-full resize-none bg-transparent pl-5 pr-14 py-4 text-body text-ink placeholder:text-ink-muted focus:outline-none min-h-[56px] max-h-[300px]"
              placeholder="Ask anything about your knowledge graph... (Shift+Enter for newline)"
              value={input}
              onChange={event => {
                setInput(event.target.value);
                event.target.style.height = 'auto';
                event.target.style.height = `${event.target.scrollHeight}px`;
              }}
              onKeyDown={handleKeyDown}
              disabled={sending}
            />

            <div className="absolute bottom-2 right-2 flex items-center justify-center shrink-0 h-10 w-10">
              <button
                type="button"
                onClick={() => void sendMessage()}
                disabled={!input.trim() || sending}
                aria-label="Send message"
                className="h-9 w-9 flex items-center justify-center rounded-xl bg-ledger text-white disabled:opacity-40 disabled:bg-ink-muted disabled:cursor-not-allowed hover:bg-ledger-dark transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-ledger focus:ring-offset-2"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="flex justify-between items-center mt-3 px-2">
            <p className="text-xs text-ink-muted font-medium">
              Answers are grounded in your knowledge graph only.
            </p>
            {input.length > 0 && (
              <span className="text-xs font-medium text-ink-muted">
                {input.length} chars
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
