import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const defaultInputPath = path.join(__dirname, 'generate', 'openapi-formatted.json')
const inputPath = process.argv[2] ? path.resolve(process.argv[2]) : defaultInputPath

const PATH_REF_PATTERN = /^(?<method>[A-Z]+)::(?<path>\/.*)$/
const PATH_PARAM_PATTERN = /^\{([^}]+)\}$/
const API_VERSION_PATTERN = /^v\d+$/i

function splitWords(value) {
	return value.split(/[^A-Za-z0-9]+/).filter(Boolean)
}

function toLowerWords(words) {
	return words.map((word) => word.toLowerCase())
}

function singularizeWord(word) {
	const lower = word.toLowerCase()

	if (lower.endsWith('ies') && lower.length > 3) {
		return lower.slice(0, -3) + 'y'
	}

	if (lower.endsWith('s') && !lower.endsWith('ss') && lower.length > 1) {
		return lower.slice(0, -1)
	}

	return lower
}

function singularizeTrailingWord(words) {
	return words.map((word, index) => (index === words.length - 1 ? singularizeWord(word) : word))

}

function matchesParamPrefix(segmentWords, paramWords) {
	if (segmentWords.length === 0 || paramWords.length === 0) {
		return false
	}

	const normalizedSegmentWords = toLowerWords(segmentWords)
	const singularizedWords = singularizeTrailingWord(normalizedSegmentWords)
	return singularizedWords.every((word, index) => paramWords[index] === word)
}

function normalizeStaticSegmentWords(segmentWords, nextParamWords) {
	if (segmentWords.length === 0) {
		return []
	}

	const normalizedSegmentWords = toLowerWords(segmentWords)
	if (!matchesParamPrefix(segmentWords, nextParamWords)) {
		return normalizedSegmentWords
	}

	return singularizeTrailingWord(normalizedSegmentWords)
}

function shouldInlineNextParam(segmentWords, nextParamWords, hasStaticSegmentAfterNextParam) {
	if (!hasStaticSegmentAfterNextParam) {
		return false
	}

	return matchesParamPrefix(segmentWords, nextParamWords)
}

function parseOperationRef(value) {
	const match = PATH_REF_PATTERN.exec(value)
	if (!match?.groups) {
		return null
	}

	return {
		method: match.groups.method.toLowerCase(),
		path: match.groups.path,
	}
}

function stripVersionPrefix(segments) {
	if (segments[0]?.toLowerCase() === 'api' && API_VERSION_PATTERN.test(segments[1] ?? '')) {
		return segments.slice(2)
	}

	return segments
}

function parsePathParam(segment) {
	const match = PATH_PARAM_PATTERN.exec(segment)
	if (!match) {
		return null
	}

	return toLowerWords(splitWords(match[1]))
}

function toCamelCaseFromTokens(tokens, fallbackValue) {
	if (tokens.length === 0) {
		return fallbackValue
	}

	return tokens
		.map((token, index) => {
			const lower = token.toLowerCase()
			if (index === 0) {
				return lower
			}

			return lower.charAt(0).toUpperCase() + lower.slice(1)
		})
		.join('')
}

function buildOperationTokens(method, segments) {
	const tokens = [method]
	const trailingParamGroups = []

	for (let index = 0; index < segments.length; index += 1) {
		const segment = segments[index]
		const currentParamWords = parsePathParam(segment)
		if (currentParamWords) {
			trailingParamGroups.push(currentParamWords)
			continue
		}

		const segmentWords = splitWords(segment)
		const nextParamWords = parsePathParam(segments[index + 1] ?? '') ?? []
		const hasStaticSegmentAfterNextParam = index + 2 < segments.length

		if (shouldInlineNextParam(segmentWords, nextParamWords, hasStaticSegmentAfterNextParam)) {
			tokens.push(...nextParamWords)
			index += 1
			continue
		}

		tokens.push(...normalizeStaticSegmentWords(segmentWords, nextParamWords))
	}

	if (trailingParamGroups.length > 0) {
		tokens.push('by', ...trailingParamGroups[0])

		for (let index = 1; index < trailingParamGroups.length; index += 1) {
			tokens.push('and', ...trailingParamGroups[index])
		}
	}

	return tokens
}

function toCamelCase(value) {
	const parsedOperationRef = parseOperationRef(value)
	if (!parsedOperationRef) {
		return toCamelCaseFromTokens(splitWords(value), value)
	}

	const segments = stripVersionPrefix(parsedOperationRef.path.split('/').filter(Boolean))
	const tokens = buildOperationTokens(parsedOperationRef.method, segments)
	return toCamelCaseFromTokens(tokens, value)
}

function polishOperationIds(document) {
	if (!document?.paths || typeof document.paths !== 'object') {
		throw new Error('OpenAPI document does not contain a valid paths object.')
	}

	for (const pathItem of Object.values(document.paths)) {
		if (!pathItem || typeof pathItem !== 'object') {
			continue
		}

		for (const operation of Object.values(pathItem)) {
			if (!operation || typeof operation !== 'object' || typeof operation.operationId !== 'string') {
				continue
			}

			operation.operationId = toCamelCase(operation.operationId)
		}
	}

	return document
}

async function main() {
	const raw = await readFile(inputPath, 'utf8')
	const document = JSON.parse(raw)
	const polishedDocument = polishOperationIds(document)

	await writeFile(inputPath, `${JSON.stringify(polishedDocument, null, 2)}\n`, 'utf8')
	console.log(`Polished operationId values in ${inputPath}`)
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : error)
	process.exitCode = 1
})
