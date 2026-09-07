# Vastgoedtool – Gambia

**Vastgoedtool** is een Streamlit-applicatie om aangekochte en verkochte percelen in Gambia te beheren en te analyseren.

## Functies
- 📍 Interactieve kaart van kavels (status: portfolio, gepland, verkocht)  
- 👥 Investeerdersbeheer: inleg, rente, winstdeling  
- 📊 Financiële analyse: winst, kapitaalkosten, rendement per perceel/investeerder  
- 🔄 Prognoses voor actieve kavels bij afwezigheid van verkopen  
- 📂 Documentupload en checklist per perceel

## Installatie
1. **Clone** de repo  
   ```bash
   git clone https://github.com/JonathanGambiaGlobal/Vastgoedtool.git
   cd Vastgoedtool
   ```

## Configuratie op Render

Stel onder **Environment** de volgende variabelen in:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `GROQ_API_KEY`
- `FXRATES_TOKEN`
- `GOOGLE_API_KEY`

Lokaal mogen dezelfde namen ook in `.streamlit/secrets.toml` staan. Zet echte
sleutels nooit in Git. Render start de app via de meegeleverde `render.yaml`.
