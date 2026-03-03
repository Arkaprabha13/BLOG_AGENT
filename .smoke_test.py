from pathlib import Path
import sys
base = Path('D:/BLOG_MANAGER')
checks = []

for f in ['agent.py','scheduler.py','render.yaml','README.md','clients/trends_client.py']:
    checks.append((f, (base/f).exists()))

bot = (base/'bot.py').read_text(encoding='utf-8')
checks.append(('bot.py cmd_agent', 'cmd_agent' in bot))
checks.append(('bot.py process_message', 'process_message' in bot))
checks.append(('bot.py _fetch_and_send_trending', '_fetch_and_send_trending' in bot))

agent_src = (base/'agent.py').read_text(encoding='utf-8')
checks.append(('agent.py process_message', 'async def process_message' in agent_src))
checks.append(('agent.py AgentResult', 'class AgentResult' in agent_src))
checks.append(('agent.py clear_history', 'clear_history' in agent_src))
checks.append(('agent.py generate_force intent', 'generate_force' in agent_src))

readme = (base/'README.md').read_text(encoding='utf-8')
checks.append(('README Deploy to Render', 'Deploy to Render' in readme))
checks.append(('README AI Agent section', 'AI Agent' in readme))
checks.append(('README architecture', 'SCOUT' in readme))

ry = (base/'render.yaml').read_text(encoding='utf-8')
checks.append(('render.yaml healthCheckPath', 'healthCheckPath' in ry))
checks.append(('render.yaml python main.py', 'python main.py' in ry))

failed = 0
for name, ok in checks:
    status = "  OK  " if ok else "  FAIL"
    print(status + " " + name)
    if not ok:
        failed += 1

print("")
print(str(len(checks)-failed) + "/" + str(len(checks)) + " checks passed")
sys.exit(0 if not failed else 1)
