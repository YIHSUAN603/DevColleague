import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { openExternal } from '../utils'

/** 顯示 LLM 回覆的 Markdown；不渲染原始 HTML，連結以外部瀏覽器開啟 */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault()
                if (href) void openExternal(href)
              }}
            >
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
