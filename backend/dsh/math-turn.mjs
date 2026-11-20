// 只挂载数学工具和内存会话。模型访问与数学状态均由 Python 工作流持有。
import { createInterface } from 'node:readline'
import { Context } from '@deepseek-ai/cordis'
import AgentRegistry from '@deepseek-ai/dsh-agent'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import Llm, { LlmAdapter, createUserMessage } from '@deepseek-ai/dsh-llm'
import Sessions from '@deepseek-ai/dsh-session'
import SessionProjections from '@deepseek-ai/dsh-session-projection'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as nativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import Tools from '@deepseek-ai/dsh-tools'

const input = createInterface({ input: process.stdin, crlfDelay: Infinity })
const lines = input[Symbol.asyncIterator]()
const write = value => process.stdout.write(JSON.stringify(value) + '\n')

async function read() {
  const line = await lines.next()
  if (line.done) throw new Error('math workflow disconnected')
  return JSON.parse(line.value)
}

function openaiMessage(message) {
  const blocks = message.content
  const text = blocks.filter(b => b.type === 'text').map(b => b.text).join('')
  const result = { role: message.role, content: text }
  if (message.role === 'tool') result.tool_call_id = message.toolCallId
  if (message.role === 'assistant') {
    const reasoning = blocks.filter(b => b.type === 'reasoning').map(b => b.text).join('')
    if (reasoning) result.reasoning_content = reasoning
    const calls = blocks.filter(b => b.type === 'tool-call')
    if (calls.length) {
      result.tool_calls = calls.map(b => ({ id: b.id, type: 'function',
        function: { name: b.name, arguments: b.arguments } }))
      if (!text) result.content = null
    }
  }
  return result
}

class WorkflowModel extends LlmAdapter {
  async *stream({ messages, tools }) {
    write({ op: 'model', history: messages.map(openaiMessage),
      tools: (tools ?? []).map(fn => ({ type: 'function', function: fn })) })
    const message = await read()
    const blocks = []
    if (message.reasoning_content) blocks.push({ type: 'reasoning', text: message.reasoning_content })
    if (message.content) blocks.push({ type: 'text', text: message.content })
    for (const call of message.tool_calls ?? []) {
      blocks.push({ type: 'tool-call', id: call.id, name: call.function.name, arguments: call.function.arguments })
    }
    for (const [index, block] of blocks.entries()) {
      yield { type: 'block-start', index, blockType: block.type }
      yield { type: 'block-end', index, block }
    }
    yield { type: 'finish', reason: { kind: message.tool_calls?.length ? 'tool-calls' : 'stop' } }
  }
}

async function run() {
  const { prompt, system_prompt, tools } = await read()
  const root = new Context()
  try {
    await root.plugin(Sessions)
    await root.plugin(SessionProjections)
    await root.plugin(Llm)
    await root.plugin(SystemPrompt, {
      personaPrefix: system_prompt, includeHarnessIdentity: false, includeRuntimeContext: false,
    })
    await root.plugin(Tools)
    await root.plugin(nativeExecutor)
    await root.plugin(AgentRegistry)
    await root.plugin(AgentLoop, { agents: [], maxParallelToolCalls: 1 })
    root.llm.registerAdapter(['mcmer'], new WorkflowModel())
    for (const { function: definition } of tools) {
      root.tools.register({
        ...definition,
        output: { schema: {}, render: (_args, result) => [{ type: 'text', text: JSON.stringify(result) }] },
        async execute(args) {
          write({ op: 'tool', name: definition.name, arguments: args })
          return await read()
        },
      })
    }
    const handle = await root.agents.create({ agentOptions: { provider: 'mcmer', model: 'workflow' } })
    try {
      handle.agent.followup(createUserMessage({ source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] }))
      await handle.agent.whenIdle()
      const events = handle.agent.session.snapshotEvents()
      const end = events.findLast(event => event.type === 'turn/end')
      if (!end) throw new Error('DeepSeek Harness ended without a turn')
      const reason = end.data.reason
      if (reason.kind !== 'completed') throw new Error(reason.error?.message ?? `turn ended: ${reason.kind}`)
    } finally {
      await handle.dispose()
    }
  } finally {
    await root.fiber.dispose()
  }
  write({ op: 'done' })
}

try {
  await run()
} catch (error) {
  write({ op: 'error', message: String(error.message) })
  process.exitCode = 1
} finally {
  input.close()
}
