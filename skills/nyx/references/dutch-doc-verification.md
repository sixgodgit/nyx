# Dutch Document Verification — Reference

## Waternet Letter Verification

**Scenario:** User received a letter claiming to be from Waternet (Amsterdam water company) but wants to verify authenticity.

**Key facts:**
- **Waternet service area:** Amsterdam and surrounding areas ONLY (not Den Haag)
- **Den Haag water company:** Dunea
- **Waternet address:** Korte Ouderkerkerdijk 7, Amsterdam
- **Waternet phone:** 0900 93 94
- **Waternet KVK:** 41216593
- **Waternet website:** waternet.nl

**Red flags in fake letters:**
1. Words glued together (Dutch words should be separate): "eerdereen" → "eerder een", "vandaageen" → "vandaag een", "kuntu" → "kunt u"
2. Odd phrasing: "Drinkwater is namelijk niet gratis" (unofficial tone)
3. URLs that don't match real Waternet paths
4. Geographic mismatch (Waternet letter sent to Den Haag address)

**Verification steps:**
1. Check sender details against known Waternet info
2. Verify service area covers the address
3. Look for LLM artifacts (word spacing, grammar)
4. Check if phone/KVK numbers match official records

## General Dutch Official Document Verification

**Techniques:**
- Cross-reference addresses with official KVK records
- Verify phone numbers against official websites
- Check service areas (many Dutch companies are region-specific)
- Look for language artifacts that suggest AI generation
