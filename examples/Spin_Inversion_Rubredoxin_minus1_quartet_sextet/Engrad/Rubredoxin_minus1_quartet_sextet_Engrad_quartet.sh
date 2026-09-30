#!/bin/bash
#PBS -N Rubredoxin_minus1_quartet_sextet_Engrad_quartet
#PBS -l nodes=1:ppn=12
#PBS -o Rubredoxin_minus1_quartet_sextet_Engrad_quartet.pbs.out
#PBS -e Rubredoxin_minus1_quartet_sextet_Engrad_quartet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Rubredoxin_minus1_quartet_sextet_Engrad_quartet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Rubredoxin_minus1_quartet_sextet_Engrad_quartet.inp > Rubredoxin_minus1_quartet_sextet_Engrad_quartet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
