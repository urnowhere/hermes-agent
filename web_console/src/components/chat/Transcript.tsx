import { MessageCard } from './MessageCard';

export interface TranscriptItem {
  role: 'user' | 'assistant' | 'tool' | 'system';
  title: string;
  content: string;
  isStreaming?: boolean;
  reasoning?: string;
  isReasoningStreaming?: boolean;
}

interface TranscriptProps {
  items: TranscriptItem[];
  sessionId?: string;
  onBranch?: (messageIndex: number) => void;
}

export function Transcript({ items, sessionId, onBranch }: TranscriptProps) {
  return (
    <section className="chat-panel" aria-label="Transcript">
      <div className="transcript-list">
        {items.map((item, index) => (
          <MessageCard
            key={`${item.role}-${index}-${item.title}`}
            role={item.role}
            title={item.title}
            content={item.content}
            isStreaming={item.isStreaming}
            reasoning={item.reasoning}
            isReasoningStreaming={item.isReasoningStreaming}
            messageIndex={index}
            onBranch={onBranch}
          />
        ))}
      </div>
    </section>
  );
}
