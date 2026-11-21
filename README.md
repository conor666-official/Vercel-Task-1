# IG Lookup - Vercel serverless

Deploy:

    npm i -g vercel
    cd vercel
    vercel            # preview
    vercel --prod     # production

Or drag the folder into vercel.com/new.

Endpoints:

    /health
    /search?ig=<username>          profile
    /search?post=<shortcode|url>   single post
    &pretty=1                      indented json

Credit: @Techno_Telex
