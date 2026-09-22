
# Initial Plan

* This is the initial plan for the note taking application I would like to create to interface with my hyprland environment

* This application should take notes using the nvim key commands, have all the same keys essentially

* What the hyprland bind key + N , to open up this note app up. 

* I want the note app to open up in focus and when it is focus it has it's own key bindings

* When focused, I want bind key + C opens up a new note which gets stored in unsorted 
    - It's just labeled today's date (note_MM-DD-YY.md) 
    - If today already has a note just make a separate note with iteration count, so note_MM-DD-YY-N.md, where N is the iteration count of the new note for the same day.

* The Note App should also have  kanban board which pops up bindkey + tab is pressed
    - The kanban board should be associated with a specific markdown file in ${HOME_VAULT}/kanban
    - The kanban board will just have a column for each project and with a task underneath
    - The first three columns are active (currently worked), monitor(watched task), route(information I need to pass along to someone)
    - Each column after that is a project related tasked
    - The last column is unknown because it has not been classified which column to put into

* The Note App should be able to auto sort notes based on specific key markings
    - If "*>" prefixes a note, it should go under the unkown column in the kanban board, where the default kanban
    - If "*COLUMN>" it should go under the column in the kanban board with the correct name
    - Update the kanban.md file so it has all these placed correctly
    - For each task under route, I would like for you to send me a notification once every hour starting from 11:00 am until 2:00 pm

* Use an activity Radar with tmux
    - See the activiy radar markdown file
    - the activty radar should be a bindkey + tab 
