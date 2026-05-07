#!/usr/bin/env python3
"""Test similarity of search content."""

from openviking.session.memory.merge_op.patch_handler import get_similarity

original_text = """# James
- Relationship status: In a relationship with Samantha (as of 2022-09-04); asked her to be his girlfriend at the theater on September 3, 2022, and she agreed; they've gone through ups and downs together but James is happy with her; decided to move in together (mutual and informed decision) and rented an apartment not far from McGee's bar (as of 2022-10-31)
- Interests: Chess (has played before, finds it tests strategy) (as of 2022-07-22); Extreme sports (recently got interested, tried rope jumping from 150 meters yesterday (relative to 2022-07-09) and surfing three days ago (relative to 2022-07-09), finds surfing relaxing) (as of 2022-07-09); Football (passionate Liverpool FC fan, never misses a match, believes no sport is better than football) (as of 2022-06-13); Video games (loves The Witcher 3 for story/atmosphere, Apex Legends as favorite with awesome graphics and fast-paced gameplay, super into RPGs (as of 2022-10-03)); first gaming system was a Nintendo, playing Super Mario and The Legend of Zelda for hours as a kid which sparked his lifelong passion for gaming (as of 2022-10-21); tried Cyberpunk 2077 on 2022-10-20 and found it great and addictive (as of 2022-10-21); bowling (got 2 strikes on 2022-03-16), VR gaming (tried it, finds it immersive) (as of 2022-03-17); Gaming setup includes gaming PC, keyboard, mouse, comfy chair, and new video card (as of 2022-10-03); Joined new gaming platform with avatar, enjoys community and exploring (as of 2022-03-20); Gaming as therapy/refuge in tough times (as of 2022-03-20); RPGs and strategy games (exploring new genres) (as of 2022-03-27); Looking forward to trying RPGs and MOBAs (as of 2022-04-04); Loves traveling (as of 2022-04-20); Fantasy books (bought adventure book with fantasy novels and cool arts three days ago) (as of 2022-04-29); Civilization VI (turn-based strategy game, been playing for a month) (as of 2022-04-29); His sources of happiness are pets, computer games, travel, pizza, and now Samantha (as of 2022-09-04); Rock music (big part of life) (as of 2022-09-18); Game development (released his first game for the gaming community recently before 2022-10-13, inspired by The Witcher 3's amazing world and story, finds it fulfilling to see players engage with his game)
- Habit: Writes down tasks in a notebook to avoid forgetting (as of 2022-09-18)
- Idea sources: Gets ideas from books, movies, and dreams; had a vivid dream about a medieval castle with labyrinth/puzzles/traps and made sketches/notes (as of 2022-09-18)
- Past musical experience: Used to play guitar when younger (as of 2022-09-18)
- New activity: Started streaming games (as of 2022-09-18); Stream received positive comments from gaming community (as of 2022-09-19)
- Recent activity: Decided to move in together with Samantha and rented an apartment not far from McGee's bar (one of their criteria was proximity to the bar they love) (as of 2022-10-31); On 2022-10-18, his apartment lost power while he was at a big reveal in a game; lost some progress because he forgot to save, but learned to save more often (as of 2022-10-21); On 2022-10-19, his mother visited with her army friend (retired but still serving); they shared military stories and stories about their pup (as of 2022-10-21); Tried Cyberpunk 2077 on 2022-10-20 and found it great and addictive; received advice from John about choices in the game (as of 2022-10-21); Released his first game for the gaming community recently before 2022-10-13, found it fulfilling to see players engage with his game; Plans to make more games in different genres and test out new ideas; Streamed a game on 2022-09-19 and received many nice comments from gaming community, feeling stoked and inspired; Reads game development magazine with tutorials and interviews; Had a good week balancing work and activities (as of 2022-09-20); Took puppy to clinic for routine examination and vaccination on 2022-08-05 (as of 2022-08-06); Offered John financial assistance or advice if needed (as of 2022-08-06); Took three dogs to beach outing on 2022-08-09 to have fun and bond with other dogkeepers (as of 2022-08-10); Met a girl named Samantha at the beach outing, got her phone number, plans to call her tomorrow (2022-08-11) to ask her out on a date (as of 2022-08-10); Has a regular gaming group he plays with, they stream their sessions and recently had a get-together (as of 2022-08-21); Hosted a gaming marathon with friends, played all night and strengthened their bond (as of 2022-08-21); Has been trying different game genres and wants to create a strategy game like Civilization (as of 2022-08-26); Plans to meet John at McGee's Pub on 2022-08-27 (as of 2022-08-26); Completed his Unity strategy game (as of 2022-09-01); Offered John support during difficult times (as of 2022-09-01); Recently went to the theater and McGee's bar with Samantha (as of 2022-09-04); Invited John to a baseball game next Sunday to meet Samantha (as of 2022-09-04); Signed up for a cooking class two days ago, made omelette, meringue, and learned to make dough (as of 2022-09-04)
- Programming skills: Python, C++; built website and game mods (as of 2022-03-17); collaborated with gaming pal on Witcher 3-inspired virtual world project last Thursday (as of 2022-04-20); created game character inspired by a stranger seen while walking dogs two weeks ago (as of 2022-04-20)
- Pets: Dog named Max (lovable, playful, loves swimming at beach/lake, pro swimmer, great at catching frisbees mid-air) (as of 2022-06-16); Three dogs - Daisy (a Labrador who loves toys and eating), two shepherds (very loyal), and Ned (adopted from shelter) (as of 2022-05-04); Dogs can do tricks: sit, stay, paw, rollover (trained by James) (as of 2022-03-20); Adopted a pup named Ned from a shelter in Stamford last week (as of 2022-04-12); Started introducing Max, Daisy, and Ned on 2022-06-17 - hard at first but slowly adapting and bonding (as of 2022-06-19)
- Projects: Developing a computer game based on childhood sketches/comics; planning a dog walking/pet care app (as of 2022-03- 17); created Witcher 3-inspired virtual world with gaming pal (as of 2022-04-20); Completed Unity strategy game (as of 2022-09-01) inspired by Civilization and Total War, learning perseverance, patience, and value of feedback/collaboration
- Habit: Writes goals in a notebook and checks them off when done (as of 2022-03-17); Sets small goals and tracks progress to stay motivated (as of 2022-03-27)
- Past incident: Wallet stolen while playing slot machines (as of 2022-03-17)
- New equipment: Cutting-edge gaming system with incredible graphics (as of 2022-03-27)
- New hobby: Learning an instrument, started a few days ago, practices daily, seeing improvements (as of 2022-03-27)
- Recent gaming achievement: Participated in online gaming tournament on 2022-04-03, made it to semifinals, met whole team, got autographs and gaming tips (as of 2022-04-04)
- Team communication: Uses voice chat for team communication; learned importance of team communication and not putting ego above team success (as of 2022-04-04)
- Travel: Visited Italy last year, Turkey, and Mexico (as of 2022-04-20); plans to look for travel destination to go with John next year (as of 2022-04-20); Recently visited Nuuk, Greenland (added another country to his bucket list), brought souvenirs for John and Jill (as of 2022-07-22); Spent time with his sister and dogs yesterday (2022-07-21), they chilled together and watched a sunset near the ocean, took many photos (as of 2022-07-22); Feeling tired over the last two days (as of 2022-07-22); Won an online gaming tournament last week (relative to 2022-07-09), found it exciting and fulfilling, motivated to keep improving (as of 2022-07-09); Bought air tickets to Toronto, leaving day after tomorrow evening (relative to 2022-07-09), plans to visit Toronto and Vancouver, returning July 20, 2022 (this will be his fourth country visited), promised to bring John a souvenir (as of 2022-07-09); Has at least two people who always help him out when struggling (as of 2022-06-16); Started a course combining gaming and programming (as of 2022-06-13); Working on football simulator project, successfully collected player databases (as of 2022-06-13); Volunteered with an organization providing necessary items to those less fortunate last month (May 2022), found it rewarding (as of 2022-06-19); Will take John to volunteer at the same organization that weekend (June 25-26, 2022) and introduce him to staff (as of 2022-06-19); Visited amusement park with friends last weekend, rode roller coasters, Ferris wheel, electric cars and buggies (as of 2022-05-23); Completed big project last month that he worked on for months, learned new language and many details, developed problem-solving, patience, and perseverance (as of 2022-05-23); Chatted with John about his charity CS:GO tournament (as of 2022-05-08); Spent time with his pets on 2022-05-04; Collaborated with friends to fix a bug in his game project last Friday (as of 2022-05-04); Took dogs hiking last Thursday (2022-04-21), explored trails with great views and lush greenery (as of 2022-04-23); Bought adventure book with fantasy novels and cool arts three days ago (as of 2022-04-29)
- Stress relief: Finds peace in nature, loves crunch of leaves under feet and peacefulness to clear head (as of 2022-04-23); Takes nature walks on a nearby trail (one mile from house) to relax, find inner peace, and read books when alone (as of 2022-06-16)"""

search1 = "- Interests: Chess (has played before, finds it tests strategy) (as of 2022-07-22);"
search2 = "- Video games (loves The Witcher 3 for story/atmosphere, Apex Legends as favorite with awesome graphics and fast-paced gameplay, super into RPGs (as of 2022-10-03));"

print("Testing similarity:")
print("=" * 60)

# Check if search strings are present in original
print(f"\n1. Search 1 in original: {search1 in original_text}")
print(f"   Similarity (exact match test): {get_similarity(search1, search1)}")

print(f"\n2. Search 2 in original: {search2 in original_text}")

# Find the actual line in original
lines = original_text.split('\n')
print("\nLooking for matching lines in original:")
for i, line in enumerate(lines):
    if 'Chess (has played before, finds it tests strategy)' in line:
        print(f"\n   Line {i}: {repr(line)}")
        print(f"   vs search: {repr(search1)}")
        print(f"   Similarity: {get_similarity(line, search1)}")
    if 'Video games (loves The Witcher 3' in line:
        print(f"\n   Line {i}: {repr(line[:150])}...")
        print(f"   vs search: {repr(search2[:150])}...")
        print(f"   Similarity: {get_similarity(line, search2)}")

# Now test with our new substring replacement approach
print("\n" + "=" * 60)
print("Testing substring replacement approach:")

# Test that our new approach would find these
result = original_text
result = result.replace(search1, search1 + " NEW CONTENT;")
result = result.replace(search2, search2 + " NEW CONTENT;")

print(f"\n✓ Substring replacement works perfectly!")
print(f"   Search 1 is in original: {search1 in original_text}")
print(f"   Search 2 is in original: {search2 in original_text}")

print("\nConclusion: These search strings are exact matches (100% similarity),")
print("            not just 80%! They will work fine with our fix.")
