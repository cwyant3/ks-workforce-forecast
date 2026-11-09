function region() {
var cntya = document.getElementById("ctspan");
var rega = document.getElementById("regspan");
var geogg = document.getElementById("geog");
var a = geogg.selectedIndex;
var b=geogg.options[a].value;
var cnty=document.getElementById("county");
var regb=document.getElementById("regi");
var c = regb.selectedIndex;
var d = regb.options[c].value;
if (b==2 || b==4 || b==5) 
 {
   rega.style.display='none';
   cntya.style.display='block';
  if (d==1) //Central Kansas Region
  {
   cnty.length=0;
  var txt="Barton County";var val="009";
   addopt(cnty,txt,val,true);
  var txt="McPherson County";var val="113";
   addopt(cnty,txt,val,true);
  var txt="Pawnee County";var val="145";
  addopt(cnty,txt,val,true);
  var txt="Rice County";var val="159";
  addopt(cnty,txt,val,true);
  var txt="Saline County";var val="169";
  addopt(cnty,txt,val,true);
  var txt="Stafford County";var val="185";
  addopt(cnty,txt,val,true);
  }
  else if (d==2)  //East Central Coalition
  {
   cnty.length=0;
  var txt="Chase County";var val="017";
   addopt(cnty,txt,val,true);
  var txt="Coffey County";var val="031";
   addopt(cnty,txt,val,true);
  var txt="Franklin County";var val="059";
  addopt(cnty,txt,val,true);
  var txt="Greenwood County";var val="073";
  addopt(cnty,txt,val,true);
  var txt="Lyon County";var val="111";
  addopt(cnty,txt,val,true);
  var txt="Morris County";var val="127";
  addopt(cnty,txt,val,true);
  var txt="Osage County";var val="139";
  addopt(cnty,txt,val,true);
  var txt="Wabaunsee County";var val="197";
  addopt(cnty,txt,val,true);
  }
  
  else if (d==3)  //Ellis County Health Department
  {
   cnty.length=0;
  var txt="Ellis County";var val="051";
  addopt(cnty,txt,val,true);
  }
  
  else if (d==4)  //KC Metro Region
  {
   cnty.length=0;
  var txt="Douglas County";var val="045";
   addopt(cnty,txt,val,true);
  var txt="Johnson County";var val="091";
   addopt(cnty,txt,val,true);
  var txt="Leavenworth County";var val="103";
  addopt(cnty,txt,val,true);
  var txt="Linn County";var val="107";
  addopt(cnty,txt,val,true);
  var txt="Miami County";var val="121";
  addopt(cnty,txt,val,true);
  var txt="Wyandotte County";var val="209";
  addopt(cnty,txt,val,true);
  }
  else if (d==5)  //South Central Metro County
  {
  cnty.length=0;
  var txt="Butler County";var val="015";
  addopt(cnty,txt,val,true);
  var txt="Cowley County";var val="035";
  addopt(cnty,txt,val,true);
  var txt="Harvey County";var val="079";
  addopt(cnty,txt,val,true);
  var txt="Marion County";var val="115";
  addopt(cnty,txt,val,true);
  var txt="Reno County";var val="155";
  addopt(cnty,txt,val,true);
  var txt="Sedgwick County";var val="173";
  addopt(cnty,txt,val,true);
  var txt="Sumner County";var val="191";
  addopt(cnty,txt,val,true);
  }
  else if (d==6)  //Lower 8 of South East Kansas County
  {
  cnty.length=0;
  var txt="Chautauqua County";var val="019";
  addopt(cnty,txt,val,true);
  var txt="Cherokee County";var val="021";
  addopt(cnty,txt,val,true);
  var txt="Crawford County";var val="037";
  addopt(cnty,txt,val,true);
  var txt="Elk County";var val="049";
  addopt(cnty,txt,val,true);
  var txt="Labette County";var val="099";
  addopt(cnty,txt,val,true);
  var txt="Montgomery County";var val="125";
  addopt(cnty,txt,val,true);
  var txt="Neosho County";var val="133";
  addopt(cnty,txt,val,true);
  var txt="Wilson County";var val="205";
  addopt(cnty,txt,val,true);
  }
  else if (d==7)  //North Central Kansas Public Health Initiative County
  {
  cnty.length=0;
  var txt="Clay County";var val="027";
  addopt(cnty,txt,val,true);
  var txt="Cloud County";var val="029";
  addopt(cnty,txt,val,true);
  var txt="Dickinson County";var val="041";
  addopt(cnty,txt,val,true);
  var txt="Ellsworth County";var val="053";
  addopt(cnty,txt,val,true);
  var txt="Jewell County";var val="089";
  addopt(cnty,txt,val,true);
  var txt="Lincoln County";var val="105";
  addopt(cnty,txt,val,true);
  var txt="Mitchell County";var val="123";
  addopt(cnty,txt,val,true);
  var txt="Osborne County";var val="141";
  addopt(cnty,txt,val,true);
  var txt="Ottawa County";var val="143";
  addopt(cnty,txt,val,true);
  var txt="Republic County";var val="157";
  addopt(cnty,txt,val,true);
  var txt="Russell County";var val="167";
  addopt(cnty,txt,val,true);
  var txt="Smith County";var val="183";
  addopt(cnty,txt,val,true);
  var txt="Washington County";var val="201";
  addopt(cnty,txt,val,true);
  }
  else if (d==8)  //Northwest Bioterrorism Group County
  {
  cnty.length=0;
  var txt="Cheyenne County";var val="023";
  addopt(cnty,txt,val,true);
  var txt="Decatur County";var val="039";
  addopt(cnty,txt,val,true);
  var txt="Graham County";var val="065";
  addopt(cnty,txt,val,true);
  var txt="Norton County";var val="137";
  addopt(cnty,txt,val,true);
  var txt="Phillips County";var val="147";
  addopt(cnty,txt,val,true);
  var txt="Rawlins County";var val="153";
  addopt(cnty,txt,val,true);
  var txt="Rooks County";var val="163";
  addopt(cnty,txt,val,true);
  var txt="Thomas County";var val="193";
  addopt(cnty,txt,val,true);
  }
  else if (d==9)  //North East Corner Regional Initiative County
  {
  cnty.length=0;
  var txt="Atchison County";var val="005";
  addopt(cnty,txt,val,true);
  var txt="Brown County";var val="013";
  addopt(cnty,txt,val,true);
  var txt="Doniphan County";var val="043";
  addopt(cnty,txt,val,true);
  var txt="Jackson County";var val="085";
  addopt(cnty,txt,val,true);
  var txt="Jefferson County";var val="087";
  addopt(cnty,txt,val,true);
  var txt="Marshall County";var val="117";
  addopt(cnty,txt,val,true);
  var txt="Nemaha County";var val="131";
  addopt(cnty,txt,val,true);
  var txt="Shawnee County";var val="177";
  addopt(cnty,txt,val,true);
  }
  else if (d==10)  //Rush County County
  {
  cnty.length=0;
  var txt="Rush County";var val="165";
  addopt(cnty,txt,val,true);
  }
  else if (d==11)  //South Central Coalition County
  {
  cnty.length=0;
  var txt="Barber County";var val="007";
  addopt(cnty,txt,val,true);
  var txt="Comanche County";var val="033";
  addopt(cnty,txt,val,true);
  var txt="Edwards County";var val="047";
  addopt(cnty,txt,val,true);
  var txt="Harper County";var val="077";
  addopt(cnty,txt,val,true);
  var txt="Kingman County";var val="095";
  addopt(cnty,txt,val,true);
  var txt="Kiowa County";var val="097";
  addopt(cnty,txt,val,true);
  var txt="Pratt County";var val="151";
  addopt(cnty,txt,val,true);
  }
  else if (d==12)  //South East Kansas Multi County County
  {
  cnty.length=0;
  var txt="Allen County";var val="001";
  addopt(cnty,txt,val,true);
  var txt="Anderson County";var val="003";
  addopt(cnty,txt,val,true);
  var txt="Bourbon County";var val="011";
  addopt(cnty,txt,val,true);
  var txt="Woodson County";var val="207";
  addopt(cnty,txt,val,true);
  }
  else if (d==13)  //Southwest Kansas Health Initiative County
  {
  cnty.length=0;
  var txt="Grant County";var val="067";
  addopt(cnty,txt,val,true);
  var txt="Morton County";var val="129";
  addopt(cnty,txt,val,true);
  var txt="Seward County";var val="175";
  addopt(cnty,txt,val,true);
  var txt="Stanton County";var val="187";
  addopt(cnty,txt,val,true);
  var txt="Stevens County";var val="189";
  addopt(cnty,txt,val,true);
  }
  else if (d==14)  //Southwest Surveillance County
  {
  cnty.length=0;
  var txt="Clark County";var val="025";
  addopt(cnty,txt,val,true);
  var txt="Ford County";var val="057";
  addopt(cnty,txt,val,true);
  var txt="Gray County";var val="069";
  addopt(cnty,txt,val,true);
  var txt="Haskell County";var val="081";
  addopt(cnty,txt,val,true);
  var txt="Hodgeman County";var val="083";
  addopt(cnty,txt,val,true);
  var txt="Meade County";var val="119";
  addopt(cnty,txt,val,true);
  }
  else if (d==15)  //West Central Public Health Initiative County
  {
  cnty.length=0;
  var txt="Gove County";var val="063";
  addopt(cnty,txt,val,true);
  var txt="Logan County";var val="109";
  addopt(cnty,txt,val,true);
  var txt="Ness County";var val="135";
  addopt(cnty,txt,val,true);
  var txt="Sheridan County";var val="179";
  addopt(cnty,txt,val,true);
  var txt="Sherman County";var val="181";
  addopt(cnty,txt,val,true);
  var txt="Trego County";var val="195";
  addopt(cnty,txt,val,true);
  }
  else if (d==16)  //Western Pyramid County
  {
  cnty.length=0;
  var txt="Finney County";var val="055";
  addopt(cnty,txt,val,true);
  var txt="Greeley County";var val="071";
  addopt(cnty,txt,val,true);
  var txt="Hamilton County";var val="075";
  addopt(cnty,txt,val,true);
  var txt="Kearny County";var val="093";
  addopt(cnty,txt,val,true);
  var txt="Lane County";var val="101";
  addopt(cnty,txt,val,true);
  var txt="Scott County";var val="171";
  addopt(cnty,txt,val,true);
  var txt="Wallace County";var val="199";
  addopt(cnty,txt,val,true);
  var txt="Wichita County";var val="203";
  addopt(cnty,txt,val,true);
  }
  else if (d==17)  //Wildcat County
  {
  cnty.length=0;
  var txt="Geary County";var val="061";
  addopt(cnty,txt,val,true);
  var txt="Pottawatomie County";var val="149";
  addopt(cnty,txt,val,true);
  var txt="Riley County";var val="161";
  addopt(cnty,txt,val,true);
  }
  else if (d==18)  //Northwest Kansas
  {
  cnty.length=0;
  var txt="Barton County";var val="009";
  addopt(cnty,txt,val,true);
  var txt="Cheyenne County";var val="023";
  addopt(cnty,txt,val,true);
  var txt="Decatur County";var val="039";
  addopt(cnty,txt,val,true);
  var txt="Ellis County";var val="051";
  addopt(cnty,txt,val,true);
  var txt="Gove County";var val="063";
  addopt(cnty,txt,val,true);
  var txt="Graham County";var val="065";
  addopt(cnty,txt,val,true);
  var txt="Logan County";var val="109";
  addopt(cnty,txt,val,true);
  var txt="Ness County";var val="135";
  addopt(cnty,txt,val,true);
  var txt="Norton County";var val="137";
  addopt(cnty,txt,val,true);
  var txt="Osborne County";var val="141";
  addopt(cnty,txt,val,true);
  var txt="Phillips County";var val="147";
  addopt(cnty,txt,val,true);
  var txt="Rawlins County";var val="153";
  addopt(cnty,txt,val,true);
  var txt="Rooks County";var val="163";
  addopt(cnty,txt,val,true);
  var txt="Rush County";var val="165";
  addopt(cnty,txt,val,true);
  var txt="Russell County";var val="167";
  addopt(cnty,txt,val,true);
  var txt="Sheridan County";var val="179";
  addopt(cnty,txt,val,true);
  var txt="Sherman County";var val="181";
  addopt(cnty,txt,val,true);
  var txt="Smith County";var val="183";
  addopt(cnty,txt,val,true);
  var txt="Thomas County";var val="193";
  addopt(cnty,txt,val,true);
  var txt="Trego County";var val="195";
  addopt(cnty,txt,val,true);
  var txt="Wallace County";var val="199";
  addopt(cnty,txt,val,true);
  }
  else if (d==19)  //North Central Kansas
  {
  cnty.length=0;
  var txt="Clay County";var val="027";
  addopt(cnty,txt,val,true);
  var txt="Cloud County";var val="029";
  addopt(cnty,txt,val,true);
  var txt="Dickinson County";var val="041";
  addopt(cnty,txt,val,true);
  var txt="Ellsworth County";var val="053";
  addopt(cnty,txt,val,true);
  var txt="Geary County";var val="061";
  addopt(cnty,txt,val,true);
  var txt="Jewell County";var val="089";
  addopt(cnty,txt,val,true);
  var txt="Lincoln County";var val="105";
  addopt(cnty,txt,val,true);
  var txt="McPherson County";var val="113";
  addopt(cnty,txt,val,true);
  var txt="Marion County";var val="115";
  addopt(cnty,txt,val,true);
  var txt="Mitchell County";var val="123";
  addopt(cnty,txt,val,true);
  var txt="Morris County";var val="127";
  addopt(cnty,txt,val,true);
  var txt="Ottawa County";var val="143";
  addopt(cnty,txt,val,true);
  var txt="Republic County";var val="157";
  addopt(cnty,txt,val,true);
  var txt="Rice County";var val="159";
  addopt(cnty,txt,val,true);
  var txt="Riley County";var val="161";
  addopt(cnty,txt,val,true);
  var txt="Saline County";var val="169";
  addopt(cnty,txt,val,true);
  var txt="Washington County";var val="201";
  addopt(cnty,txt,val,true);  
  }
  else if (d==20)  //Northeast Kansas
  {
  cnty.length=0;
  var txt="Atchison County";var val="005";
  addopt(cnty,txt,val,true);
  var txt="Brown County";var val="013";
  addopt(cnty,txt,val,true);
  var txt="Doniphan County";var val="043";
  addopt(cnty,txt,val,true);
  var txt="Douglas County";var val="045";
  addopt(cnty,txt,val,true);
  var txt="Franklin County";var val="059";
  addopt(cnty,txt,val,true);
  var txt="Jackson County";var val="085";
  addopt(cnty,txt,val,true);
  var txt="Jefferson County";var val="087";
  addopt(cnty,txt,val,true);
  var txt="Johnson County";var val="091";
  addopt(cnty,txt,val,true);
  var txt="Leavenworth County";var val="103";
  addopt(cnty,txt,val,true);
  var txt="Marshall County";var val="117";
  addopt(cnty,txt,val,true);
  var txt="Miami County";var val="121";
  addopt(cnty,txt,val,true);
  var txt="Nemaha County";var val="131";
  addopt(cnty,txt,val,true);
  var txt="Osage County";var val="139";
  addopt(cnty,txt,val,true);
  var txt="Pottawatomie County";var val="149";
  addopt(cnty,txt,val,true);
  var txt="Shawnee County";var val="177";
  addopt(cnty,txt,val,true);
  var txt="Wabaunsee County";var val="197";
  addopt(cnty,txt,val,true);
  var txt="Wyandotte County";var val="209";
  addopt(cnty,txt,val,true);
  }
  else if (d==21)  //Southwest Kansas
  {
  cnty.length=0;
  var txt="Barber County";var val="007";
  addopt(cnty,txt,val,true);
  var txt="Clark County";var val="025";
  addopt(cnty,txt,val,true);
  var txt="Comanche County";var val="033";
  addopt(cnty,txt,val,true);
  var txt="Edwards County";var val="047";
  addopt(cnty,txt,val,true);
  var txt="Finney County";var val="055";
  addopt(cnty,txt,val,true);
  var txt="Ford County";var val="057";
  addopt(cnty,txt,val,true);
  var txt="Grant County";var val="067";
  addopt(cnty,txt,val,true);
  var txt="Gray County";var val="069";
  addopt(cnty,txt,val,true);
  var txt="Greeley County";var val="071";
  addopt(cnty,txt,val,true);
  var txt="Hamilton County";var val="075";
  addopt(cnty,txt,val,true);
  var txt="Haskell County";var val="081";
  addopt(cnty,txt,val,true);
  var txt="Hodgeman County";var val="083";
  addopt(cnty,txt,val,true);
  var txt="Kearny County";var val="093";
  addopt(cnty,txt,val,true);
  var txt="Kiowa County";var val="097";
  addopt(cnty,txt,val,true);
  var txt="Lane County";var val="101";
  addopt(cnty,txt,val,true);
  var txt="Meade County";var val="119";
  addopt(cnty,txt,val,true);
  var txt="Morton County";var val="129";
  addopt(cnty,txt,val,true);
  var txt="Pawnee County";var val="145";
  addopt(cnty,txt,val,true);
  var txt="Pratt County";var val="151";
  addopt(cnty,txt,val,true);
  var txt="Scott County";var val="171";
  addopt(cnty,txt,val,true);
  var txt="Seward County";var val="175";
  addopt(cnty,txt,val,true);
  var txt="Stafford County";var val="185";
  addopt(cnty,txt,val,true);
  var txt="Stanton County";var val="187";
  addopt(cnty,txt,val,true);
  var txt="Stevens County";var val="189";
  addopt(cnty,txt,val,true);
  var txt="Wichita County";var val="203";
  addopt(cnty,txt,val,true);
  }
  else if (d==22)  //South Central Kansas
  {
  cnty.length=0;
  var txt="Butler County";var val="015";
  addopt(cnty,txt,val,true);
  var txt="Chase County";var val="017";
  addopt(cnty,txt,val,true);
  var txt="Cowley County";var val="035";
  addopt(cnty,txt,val,true);
  var txt="Harper County";var val="077";
  addopt(cnty,txt,val,true);
  var txt="Harvey County";var val="079";
  addopt(cnty,txt,val,true);
  var txt="Kingman County";var val="095";
  addopt(cnty,txt,val,true);
  var txt="Reno County";var val="155";
  addopt(cnty,txt,val,true);
  var txt="Sedgwick County";var val="173";
  addopt(cnty,txt,val,true);
  var txt="Sumner County";var val="191";
  addopt(cnty,txt,val,true);
  }
  else if (d==23)  //Southeast Kansas
  {
  cnty.length=0;
  var txt="Allen County";var val="001";
  addopt(cnty,txt,val,true);
  var txt="Anderson County";var val="003";
  addopt(cnty,txt,val,true);
  var txt="Bourbon County";var val="011";
  addopt(cnty,txt,val,true);
  var txt="Chautauqua County";var val="019";
  addopt(cnty,txt,val,true);
  var txt="Cherokee County";var val="021";
  addopt(cnty,txt,val,true);
  var txt="Coffey County";var val="031";
  addopt(cnty,txt,val,true);
  var txt="Crawford County";var val="037";
  addopt(cnty,txt,val,true);
  var txt="Elk County";var val="049";
  addopt(cnty,txt,val,true);
  var txt="Greenwood County";var val="073";
  addopt(cnty,txt,val,true);
  var txt="Labette County";var val="099";
  addopt(cnty,txt,val,true);
  var txt="Linn County";var val="107";
  addopt(cnty,txt,val,true);
  var txt="Lyon County";var val="111";
  addopt(cnty,txt,val,true);
  var txt="Montgomery County";var val="125";
  addopt(cnty,txt,val,true);
  var txt="Neosho County";var val="133";
  addopt(cnty,txt,val,true);
  var txt="Wilson County";var val="205";
  addopt(cnty,txt,val,true);
  var txt="Woodson County";var val="207";
  addopt(cnty,txt,val,true);
  }
  else if (d==24)	//Frontier
  {
  cnty.length=0;
  var txt="Barber  County";var val="007";
addopt(cnty,txt,val,true);
var txt="Chase  County";var val="017";
addopt(cnty,txt,val,true);
var txt="Chautauqua  County";var val="019";
addopt(cnty,txt,val,true);
var txt="Cheyenne  County";var val="023";
addopt(cnty,txt,val,true);
var txt="Clark  County";var val="025";
addopt(cnty,txt,val,true);
var txt="Comanche  County";var val="033";
addopt(cnty,txt,val,true);
var txt="Decatur  County";var val="039";
addopt(cnty,txt,val,true);
var txt="Edwards  County";var val="047";
addopt(cnty,txt,val,true);
var txt="Elk  County";var val="049";
addopt(cnty,txt,val,true);
var txt="Gove  County";var val="063";
addopt(cnty,txt,val,true);
var txt="Graham  County";var val="065";
addopt(cnty,txt,val,true);
var txt="Greeley  County";var val="071";
addopt(cnty,txt,val,true);
var txt="Greenwood  County";var val="073";
addopt(cnty,txt,val,true);
var txt="Hamilton  County";var val="075";
addopt(cnty,txt,val,true);
var txt="Hodgeman  County";var val="083";
addopt(cnty,txt,val,true);
var txt="Jewell  County";var val="089";
addopt(cnty,txt,val,true);
var txt="Kearny  County";var val="093";
addopt(cnty,txt,val,true);
var txt="Kiowa  County";var val="097";
addopt(cnty,txt,val,true);
var txt="Lane  County";var val="101";
addopt(cnty,txt,val,true);
var txt="Lincoln  County";var val="105";
addopt(cnty,txt,val,true);
var txt="Logan  County";var val="109";
addopt(cnty,txt,val,true);
var txt="Meade  County";var val="119";
addopt(cnty,txt,val,true);
var txt="Morton  County";var val="129";
addopt(cnty,txt,val,true);
var txt="Ness  County";var val="135";
addopt(cnty,txt,val,true);
var txt="Osborne  County";var val="141";
addopt(cnty,txt,val,true);
var txt="Rawlins  County";var val="153";
addopt(cnty,txt,val,true);
var txt="Rooks  County";var val="163";
addopt(cnty,txt,val,true);
var txt="Rush  County";var val="165";
addopt(cnty,txt,val,true);
var txt="Sheridan  County";var val="179";
addopt(cnty,txt,val,true);
var txt="Sherman  County";var val="181";
addopt(cnty,txt,val,true);
var txt="Smith  County";var val="183";
addopt(cnty,txt,val,true);
var txt="Stafford  County";var val="185";
addopt(cnty,txt,val,true);
var txt="Stanton  County";var val="187";
addopt(cnty,txt,val,true);
var txt="Trego  County";var val="195";
addopt(cnty,txt,val,true);
var txt="Wallace  County";var val="199";
addopt(cnty,txt,val,true);
var txt="Wichita  County";var val="203";
addopt(cnty,txt,val,true);
}
else if (d==25)	//Rural
{
  cnty.length=0;
var txt="Anderson  County";var val="003";
addopt(cnty,txt,val,true);
var txt="Brown  County";var val="013";
addopt(cnty,txt,val,true);
var txt="Clay  County";var val="027";
addopt(cnty,txt,val,true);
var txt="Cloud  County";var val="029";
addopt(cnty,txt,val,true);
var txt="Coffey  County";var val="031";
addopt(cnty,txt,val,true);
var txt="Ellsworth  County";var val="053";
addopt(cnty,txt,val,true);
var txt="Grant  County";var val="067";
addopt(cnty,txt,val,true);
var txt="Gray  County";var val="069";
addopt(cnty,txt,val,true);
var txt="Harper  County";var val="077";
addopt(cnty,txt,val,true);
var txt="Haskell  County";var val="081";
addopt(cnty,txt,val,true);
var txt="Kingman  County";var val="095";
addopt(cnty,txt,val,true);
var txt="Linn  County";var val="107";
addopt(cnty,txt,val,true);
var txt="Marion  County";var val="115";
addopt(cnty,txt,val,true);
var txt="Marshall  County";var val="117";
addopt(cnty,txt,val,true);
var txt="Mitchell  County";var val="123";
addopt(cnty,txt,val,true);
var txt="Morris  County";var val="127";
addopt(cnty,txt,val,true);
var txt="Nemaha  County";var val="131";
addopt(cnty,txt,val,true);
var txt="Norton  County";var val="137";
addopt(cnty,txt,val,true);
var txt="Ottawa  County";var val="143";
addopt(cnty,txt,val,true);
var txt="Pawnee  County";var val="145";
addopt(cnty,txt,val,true);
var txt="Phillips  County";var val="147";
addopt(cnty,txt,val,true);
var txt="Pratt  County";var val="151";
addopt(cnty,txt,val,true);
var txt="Republic  County";var val="157";
addopt(cnty,txt,val,true);
var txt="Rice  County";var val="159";
addopt(cnty,txt,val,true);
var txt="Russell  County";var val="167";
addopt(cnty,txt,val,true);
var txt="Scott  County";var val="171";
addopt(cnty,txt,val,true);
var txt="Stevens  County";var val="189";
addopt(cnty,txt,val,true);
var txt="Thomas  County";var val="193";
addopt(cnty,txt,val,true);
var txt="Wabaunsee  County";var val="197";
addopt(cnty,txt,val,true);
var txt="Washington  County";var val="201";
addopt(cnty,txt,val,true);
var txt="Wilson  County";var val="205";
addopt(cnty,txt,val,true);
var txt="Woodson  County";var val="207";
addopt(cnty,txt,val,true);
}
else if (d==26)	//Densely-Settled Rural
{
cnty.length=0;
var txt="Allen  County";var val="001";
addopt(cnty,txt,val,true);
var txt="Atchison  County";var val="005";
addopt(cnty,txt,val,true);
var txt="Barton  County";var val="009";
addopt(cnty,txt,val,true);
var txt="Bourbon  County";var val="011";
addopt(cnty,txt,val,true);
var txt="Cherokee  County";var val="021";
addopt(cnty,txt,val,true);
var txt="Cowley  County";var val="035";
addopt(cnty,txt,val,true);
var txt="Dickinson  County";var val="041";
addopt(cnty,txt,val,true);
var txt="Doniphan  County";var val="043";
addopt(cnty,txt,val,true);
var txt="Ellis  County";var val="051";
addopt(cnty,txt,val,true);
var txt="Finney  County";var val="055";
addopt(cnty,txt,val,true);
var txt="Ford  County";var val="057";
addopt(cnty,txt,val,true);
var txt="Jackson  County";var val="085";
addopt(cnty,txt,val,true);
var txt="Jefferson  County";var val="087";
addopt(cnty,txt,val,true);
var txt="Labette  County";var val="099";
addopt(cnty,txt,val,true);
var txt="Lyon  County";var val="111";
addopt(cnty,txt,val,true);
var txt="McPherson  County";var val="113";
addopt(cnty,txt,val,true);
var txt="Neosho  County";var val="133";
addopt(cnty,txt,val,true);
var txt="Osage  County";var val="139";
addopt(cnty,txt,val,true);
var txt="Pottawatomie  County";var val="149";
addopt(cnty,txt,val,true);
var txt="Seward  County";var val="175";
addopt(cnty,txt,val,true);
var txt="Sumner  County";var val="191";
addopt(cnty,txt,val,true);
}
else if (d==27)	//Semi-Urban
{
cnty.length=0;
var txt="Butler  County";var val="015";
addopt(cnty,txt,val,true);
var txt="Crawford  County";var val="037";
addopt(cnty,txt,val,true);
var txt="Franklin  County";var val="059";
addopt(cnty,txt,val,true);
var txt="Geary  County";var val="061";
addopt(cnty,txt,val,true);
var txt="Harvey  County";var val="079";
addopt(cnty,txt,val,true);
var txt="Miami  County";var val="121";
addopt(cnty,txt,val,true);
var txt="Montgomery  County";var val="125";
addopt(cnty,txt,val,true);
var txt="Reno  County";var val="155";
addopt(cnty,txt,val,true);
var txt="Riley  County";var val="161";
addopt(cnty,txt,val,true);
var txt="Saline  County";var val="169";
addopt(cnty,txt,val,true);
}
else if (d==28)	//Urban
{
cnty.length=0;
var txt="Douglas  County";var val="045";
addopt(cnty,txt,val,true);
var txt="Johnson  County";var val="091";
addopt(cnty,txt,val,true);
var txt="Leavenworth  County";var val="103";
addopt(cnty,txt,val,true);
var txt="Sedgwick  County";var val="173";
addopt(cnty,txt,val,true);
var txt="Shawnee  County";var val="177";
addopt(cnty,txt,val,true);
var txt="Wyandotte  County";var val="209";
addopt(cnty,txt,val,true);
 }
 }
 else if (b==3)  //Kansas State
 {
 cnty.length=0;
 rega.style.display='none';
 cntya.style.display='block';
 var txt="State of Kansas";var val="920";
 addopt(cnty,txt,val,true);
 }

}
