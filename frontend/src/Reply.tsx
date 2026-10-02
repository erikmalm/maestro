import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function Reply({
  text,
  animate,
}: {
  text: string;
  animate: boolean;
}) {
  const [visible, setVisible] = useState(animate ? 0 : text.length);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(false);

  useLayoutEffect(() => {
    const element = content.current;
    if (
      following.current &&
      element &&
      element.getBoundingClientRect().bottom > window.innerHeight
    )
      element.scrollIntoView({ block: "end", behavior: "instant" });
  }, [visible]);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!animate || motion.matches) {
      setVisible(text.length);
      return;
    }
    const ends = Array.from(
      text.matchAll(/\S+\s*/g),
      (word) => word.index + word[0].length,
    );
    if (!ends.length) {
      setVisible(text.length);
      return;
    }
    const duration = Math.min(8000, ends.length * 28);
    const started = performance.now();
    let frame: number;
    function reveal(now: number) {
      const bottom =
        content.current?.getBoundingClientRect().bottom ?? Infinity;
      following.current = bottom >= 0 && bottom <= window.innerHeight + 32;
      const count = Math.max(
        1,
        Math.ceil((ends.length * (now - started)) / duration),
      );
      setVisible(ends[count - 1] ?? text.length);
      if (count < ends.length) frame = requestAnimationFrame(reveal);
    }
    function finish() {
      if (motion.matches) {
        cancelAnimationFrame(frame);
        setVisible(text.length);
      }
    }
    setVisible(0);
    frame = requestAnimationFrame(reveal);
    motion.addEventListener("change", finish);
    return () => {
      cancelAnimationFrame(frame);
      motion.removeEventListener("change", finish);
    };
  }, [text, animate]);

  return (
    <div
      ref={content}
      className="message-markdown"
      aria-busy={visible < text.length}
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => <span>{alt}</span>,
        }}
      >
        {text.slice(0, visible)}
      </Markdown>
    </div>
  );
}
