// Slash-command-aware autocomplete over pi-tui's combined provider.
//
// pi-tui reports a command's *entire* argument string as the completion
// prefix (e.g. `set ` for `/keys set␣`), and its apply step replaces that
// whole prefix with the accepted item. That is correct for single-word
// arguments, but multi-stage commands (`/keys set KEY`, `/jobs …`) complete
// only the *last* token — so accepting the second suggestion deleted the
// previous word (`/keys set␣` + `OPENROUTER_API_KEY` → `/keys
// OPENROUTER_API_KEY`). This subclass narrows the argument prefix to the
// token under the cursor; every other branch (@-mentions, paths, quoted
// prefixes, bare command names) passes through untouched. A bare `/` shows a
// short, curated quick palette; any typed character searches the full registry.
import {
	type AutocompleteItem,
	type AutocompleteSuggestions,
	type SlashCommand,
	CombinedAutocompleteProvider,
} from "@earendil-works/pi-tui";

export class ArgumentAwareAutocompleteProvider extends CombinedAutocompleteProvider {
	private readonly quickCommands: (SlashCommand | AutocompleteItem)[];

	constructor(
		commands: (SlashCommand | AutocompleteItem)[] | undefined,
		basePath: string,
		fdPath: string | null = null,
		quickCommands: (SlashCommand | AutocompleteItem)[] = [],
	) {
		super(commands, basePath, fdPath);
		this.quickCommands = quickCommands;
	}

	override async getSuggestions(
		lines: string[],
		cursorLine: number,
		cursorCol: number,
		options: { signal: AbortSignal; force?: boolean },
	): Promise<AutocompleteSuggestions | null> {
		const currentLine = lines[cursorLine] ?? "";
		const before = currentLine.slice(0, cursorCol);
		if (!options.force && before.trimStart() === "/" && this.quickCommands.length) {
			return {
				items: this.quickCommands.map((command) => this.quickItem(command)),
				prefix: "/",
			};
		}
		const result = await super.getSuggestions(lines, cursorLine, cursorCol, options);
		if (!result) return result;
		const commandText = before.trimStart();
		if (!commandText.startsWith("/")) return result;
		const spaceIndex = commandText.indexOf(" ");
		if (spaceIndex === -1) return result;
		// Only adjust results from the command-argument branch, identified by
		// the prefix being exactly the argument string (not a path/quote/@).
		const argumentText = commandText.slice(spaceIndex + 1);
		if (result.prefix !== argumentText) return result;
		const token = argumentText.slice(argumentText.lastIndexOf(" ") + 1);
		if (token === result.prefix) return result;
		return { items: result.items, prefix: token };
	}

	private quickItem(command: SlashCommand | AutocompleteItem): AutocompleteItem {
		const name = "name" in command ? command.name : command.value;
		const hint = "argumentHint" in command ? command.argumentHint : undefined;
		const description = command.description ?? "";
		const detail = hint ? (description ? `${hint} - ${description}` : hint) : description;
		return {
			value: name,
			label: name,
			...(detail ? { description: detail } : {}),
		};
	}
}
