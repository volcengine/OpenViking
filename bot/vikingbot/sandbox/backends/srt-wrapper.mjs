#!/usr/bin/env node
/**
 * SRT (Sandbox Runtime) Node.js wrapper for Python IPC
 * 
 * This script provides an IPC interface between Python and @anthropic-ai/sandbox-runtime
 * through JSON messages over stdin/stdout.
 */

import { SandboxManager } from '@anthropic-ai/sandbox-runtime';

let initialized = false;

// Process incoming messages from stdin
process.stdin.setEncoding('utf8');

let buffer = '';

process.stdin.on('data', (chunk) => {
  buffer += chunk;
  const lines = buffer.split('\n');
  buffer = lines.pop() || '';
  
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const message = JSON.parse(line);
      handleMessage(message);
    } catch (error) {
      sendError('Failed to parse message: ' + error.message);
    }
  }
});

process.stdin.on('end', () => {
  if (buffer.trim()) {
    try {
      const message = JSON.parse(buffer);
      handleMessage(message);
    } catch (error) {
      sendError('Failed to parse final message: ' + error.message);
    }
  }
});

async function handleMessage(message) {
  try {
    switch (message.type) {
      case 'initialize':
        await initialize(message.config);
        break;
      case 'execute':
        await executeCommand(message.command, message.timeout, message.customConfig);
        break;
      case 'update_config':
        updateConfig(message.config);
        break;
      case 'get_proxy_ports':
        getProxyPorts();
        break;
      case 'reset':
        await reset();
        break;
      case 'ping':
        sendResponse({ type: 'pong' });
        break;
      default:
        sendError('Unknown message type: ' + message.type);
    }
  } catch (error) {
    sendError(error.message);
  }
}

async function initialize(config) {
  if (initialized) {
    sendError('Already initialized');
    return;
  }
  
  // Check dependencies first
  const deps = SandboxManager.checkDependencies();
  if (deps.errors.length > 0) {
    sendResponse({
      type: 'initialize_failed',
      errors: deps.errors,
      warnings: deps.warnings
    });
    return;
  }
  
  try {
    await SandboxManager.initialize(config);
    initialized = true;
    
    sendResponse({
      type: 'initialized',
      warnings: deps.warnings
    });
  } catch (error) {
    sendResponse({
      type: 'initialize_failed',
      errors: [error.message]
    });
  }
}

async function executeCommand(command, timeout, customConfig) {
  if (!initialized) {
    sendError('Not initialized');
    return;
  }
  
  try {
    const sandboxedCommand = await SandboxManager.wrapWithSandbox(
      command,
      undefined,
      customConfig
    );
    
    // Execute the sandboxed command
    const { exec } = await import('child_process');
    const { promisify } = await import('util');
    const execAsync = promisify(exec);
    
    let stdout = '';
    let stderr = '';
    let exitCode = 0;
    
    try {
      const result = await execAsync(sandboxedCommand, {
        timeout: timeout || 60000,
        cwd: process.argv[3] || process.cwd()
      });
      stdout = result.stdout;
      stderr = result.stderr;
      exitCode = 0;
    } catch (error) {
      stdout = error.stdout || '';
      stderr = error.stderr || '';
      exitCode = error.code || 1;
    }
    
    // Get violations
    const violationStore = SandboxManager.getSandboxViolationStore();
    const violations = violationStore.getViolationsForCommand(command);
    
    sendResponse({
      type: 'executed',
      stdout,
      stderr,
      exitCode,
      violations: violations.map(v => ({
        line: v.line,
        timestamp: v.timestamp.toISOString(),
        command: v.command
      }))
    });
  } catch (error) {
    sendError('Execution failed: ' + error.message);
  }
}

function updateConfig(config) {
  if (!initialized) {
    sendError('Not initialized');
    return;
  }
  
  SandboxManager.updateConfig(config);
  sendResponse({ type: 'config_updated' });
}

function getProxyPorts() {
  if (!initialized) {
    sendError('Not initialized');
    return;
  }
  
  const httpProxyPort = SandboxManager.getProxyPort();
  const socksProxyPort = SandboxManager.getSocksProxyPort();
  
  sendResponse({
    type: 'proxy_ports',
    httpProxyPort,
    socksProxyPort
  });
}

async function reset() {
  if (!initialized) {
    sendError('Not initialized');
    return;
  }
  
  try {
    await SandboxManager.reset();
    initialized = false;
    sendResponse({ type: 'reset' });
  } catch (error) {
    sendError('Reset failed: ' + error.message);
  }
}

function sendResponse(response) {
  process.stdout.write(JSON.stringify(response) + '\n');
}

function sendError(message) {
  sendResponse({
    type: 'error',
    message
  });
}

// Handle graceful shutdown
process.on('SIGINT', async () => {
  if (initialized) {
    try {
      await SandboxManager.reset();
    } catch (error) {
      // Ignore cleanup errors on shutdown
    }
  }
  process.exit(0);
});

process.on('SIGTERM', async () => {
  if (initialized) {
    try {
      await SandboxManager.reset();
    } catch (error) {
      // Ignore cleanup errors on shutdown
    }
  }
  process.exit(0);
});

// Send ready signal
sendResponse({ type: 'ready' });
